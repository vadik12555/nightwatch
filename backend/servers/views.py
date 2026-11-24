"""Web UI views. Pages are server-rendered; HTMX re-fetches small partials for live updates."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from metrics.charts import DEFAULT_RANGE, RANGES, build_series
from metrics.models import LogEntry, MetricSnapshot

from .forms import ServerForm
from .models import Server

SESSION_TOKEN_KEY = "nightwatch_new_token"  # noqa: S105 - a session key name
LOG_LIMIT = 100
LOG_LEVEL_ORDER = [level for level, _ in LogEntry.Level.choices]
# Tells HTMX to stop polling (https://htmx.org/docs/#polling).
HTMX_STOP_POLLING = 286


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def public_base_url(request: HttpRequest) -> str:
    return settings.NIGHTWATCH_PUBLIC_URL or request.build_absolute_uri("/").rstrip("/")


def install_command(request: HttpRequest, token: str) -> str:
    base = public_base_url(request)
    return f"curl -fsSL {base}/agent/install.sh | sudo bash -s -- --url {base} --token {token}"


def servers_with_latest_metrics() -> list[Server]:
    """All servers with their latest snapshot and failed-services count in 2 queries (no N+1)."""
    latest_id = (
        MetricSnapshot.objects.filter(server=OuterRef("pk")).order_by("-timestamp").values("pk")[:1]
    )
    servers = list(
        Server.objects.annotate(
            latest_snapshot_id=Subquery(latest_id),
            failed_services=Count("services", filter=Q(services__status="failed")),
        )
    )
    snapshots = MetricSnapshot.objects.in_bulk(
        [s.latest_snapshot_id for s in servers if s.latest_snapshot_id]
    )
    for server in servers:
        server.latest = snapshots.get(server.latest_snapshot_id)
    return servers


# --- Dashboard ----------------------------------------------------------------------


@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    servers = servers_with_latest_metrics()
    statuses = [s.status for s in servers]
    context = {
        "servers": servers,
        "summary": {
            "total": len(servers),
            "online": statuses.count(Server.Status.ONLINE),
            "offline": statuses.count(Server.Status.OFFLINE),
            "pending": statuses.count(Server.Status.PENDING),
            "failed_services": sum(s.failed_services for s in servers),
        },
    }
    template = (
        "servers/partials/dashboard_body.html" if is_htmx(request) else "servers/dashboard.html"
    )
    return render(request, template, context)


# --- Add server / token -------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def server_create(request: HttpRequest) -> HttpResponse:
    form = ServerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        server, token = Server.create_with_token(
            name=form.cleaned_data["name"], created_by=request.user
        )
        # Post/Redirect/Get: the raw token survives the redirect in the session and is
        # removed on first display, so a page refresh never shows it again.
        request.session[SESSION_TOKEN_KEY] = {"server_id": server.pk, "token": token}
        return redirect("servers:install", pk=server.pk)
    return render(request, "servers/server_form.html", {"form": form})


@require_GET
def server_install(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(Server, pk=pk)
    token = None
    stored = request.session.get(SESSION_TOKEN_KEY)
    if stored and stored.get("server_id") == server.pk:
        token = stored["token"]
        del request.session[SESSION_TOKEN_KEY]

    context = {
        "server": server,
        "token": token,
        "command": install_command(request, token or "<TOKEN>"),
        "base_url": public_base_url(request),
    }
    return render(request, "servers/server_install.html", context)


@require_POST
def server_rotate_token(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(Server, pk=pk)
    token = server.rotate_token()
    request.session[SESSION_TOKEN_KEY] = {"server_id": server.pk, "token": token}
    messages.warning(request, "Токен перевыпущен. Старый токен больше не работает.")
    return redirect("servers:install", pk=server.pk)


@require_POST
def server_delete(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(Server, pk=pk)
    name = server.name
    server.delete()
    messages.success(request, f"Сервер «{name}» удалён вместе с его метриками и логами.")
    return redirect("servers:dashboard")


# --- Server page ------------------------------------------------------------------------------


def _latest_snapshot(server: Server) -> MetricSnapshot | None:
    return server.snapshots.order_by("-timestamp").first()


@require_GET
def server_detail(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(Server, pk=pk)
    context = {
        "server": server,
        "snapshot": _latest_snapshot(server),
        "services": server.services.all(),
        "logs": server.logs.all()[:LOG_LIMIT],
        "ranges": RANGES,
        "default_range": DEFAULT_RANGE,
        "levels": LogEntry.Level.choices,
    }
    return render(request, "servers/server_detail.html", context)


@require_GET
def server_status(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(Server, pk=pk)
    return render(request, "servers/partials/status_pill.html", {"server": server, "live": True})


@require_GET
def server_install_status(request: HttpRequest, pk: int) -> HttpResponse:
    """Polled by the install page until the first report arrives."""
    server = get_object_or_404(Server, pk=pk)
    response = render(request, "servers/partials/install_status.html", {"server": server})
    if server.last_seen is not None:
        response.status_code = HTMX_STOP_POLLING
    return response


@require_GET
def server_stats(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(Server, pk=pk)
    context = {"server": server, "snapshot": _latest_snapshot(server)}
    return render(request, "servers/partials/stats.html", context)


@require_GET
def server_services(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(Server, pk=pk)
    return render(request, "servers/partials/services.html", {"services": server.services.all()})


@require_GET
def server_logs(request: HttpRequest, pk: int) -> HttpResponse:
    """Latest log lines; `level` is a minimum severity (warning = warning+error+critical)."""
    server = get_object_or_404(Server, pk=pk)
    logs = server.logs.all()

    level = request.GET.get("level", "")
    if level in LOG_LEVEL_ORDER:
        logs = logs.filter(level__in=LOG_LEVEL_ORDER[LOG_LEVEL_ORDER.index(level) :])
    query = request.GET.get("q", "").strip()
    if query:
        logs = logs.filter(Q(message__icontains=query) | Q(source__icontains=query))

    return render(
        request,
        "servers/partials/logs.html",
        {"logs": logs[:LOG_LIMIT], "filtered": bool(level or query)},
    )


@require_GET
def server_metrics(request: HttpRequest, pk: int) -> JsonResponse:
    server = get_object_or_404(Server, pk=pk)
    range_key = request.GET.get("range", DEFAULT_RANGE)
    if range_key not in RANGES:
        return JsonResponse({"error": f"range must be one of {', '.join(RANGES)}"}, status=400)
    return JsonResponse(build_series(server, range_key))


# --- Agent distribution ---------------------------------------------------------------------


def _agent_files() -> dict[str, tuple]:
    agent_dir, deploy_dir = settings.NIGHTWATCH_AGENT_DIR, settings.NIGHTWATCH_DEPLOY_DIR
    return {
        "install.sh": (agent_dir / "install.sh", "text/x-shellscript"),
        "nightwatch_agent.py": (agent_dir / "nightwatch_agent.py", "text/x-python"),
        "requirements.txt": (agent_dir / "requirements.txt", "text/plain"),
        "config.example.yaml": (agent_dir / "config.example.yaml", "text/yaml"),
        "nightwatch-agent.service": (deploy_dir / "nightwatch-agent.service", "text/plain"),
    }


@require_GET
@login_not_required
def agent_file(request: HttpRequest, filename: str) -> FileResponse:
    """Serves the agent and its installer for `curl ... | sudo bash`. Whitelisted names only."""
    entry = _agent_files().get(filename)
    if entry is None or not entry[0].is_file():
        raise Http404("Unknown agent file")
    path, content_type = entry
    response = FileResponse(path.open("rb"), content_type=f"{content_type}; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response
