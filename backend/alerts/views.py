from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from servers.models import Server
from servers.views import is_htmx

from .forms import AlertRuleForm
from .models import Alert, AlertRule

PAGE_SIZE = 50
STATUS_TABS = [("open", "Активные"), ("resolved", "История"), ("all", "Все")]


@require_GET
def alert_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "open")
    if status not in dict(STATUS_TABS):
        status = "open"
    alerts = Alert.objects.select_related("server", "rule")
    if status != "all":
        alerts = alerts.filter(status=status)

    server_id = request.GET.get("server", "")
    if server_id.isdigit():
        alerts = alerts.filter(server_id=int(server_id))

    page = Paginator(alerts, PAGE_SIZE).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "status": status,
        "tabs": STATUS_TABS,
        "server_id": server_id,
        "servers": Server.objects.only("pk", "name"),
        "open_count": Alert.objects.open().count(),
    }
    template = "alerts/partials/alert_table.html" if is_htmx(request) else "alerts/alert_list.html"
    return render(request, template, context)


# --- Rules -----------------------------------------------------------------------------


@require_GET
def rule_list(request: HttpRequest) -> HttpResponse:
    rules = AlertRule.objects.select_related("server")
    offline_minutes = int(settings.NIGHTWATCH_OFFLINE_AFTER.total_seconds() // 60)
    return render(
        request, "alerts/rule_list.html", {"rules": rules, "offline_minutes": offline_minutes}
    )


@require_http_methods(["GET", "POST"])
def rule_edit(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    rule = get_object_or_404(AlertRule, pk=pk) if pk else None
    form = AlertRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        verb = "сохранено" if rule else "создано"
        messages.success(request, f"Правило «{saved.name}» {verb}.")
        return redirect("alerts:rules")
    return render(request, "alerts/rule_form.html", {"form": form, "rule": rule})


@require_POST
def rule_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(AlertRule, pk=pk)
    rule.is_enabled = not rule.is_enabled
    rule.save(update_fields=["is_enabled"])
    state = (
        "включено" if rule.is_enabled else "выключено — его алерты закроются при следующей проверке"
    )
    messages.info(request, f"Правило «{rule.name}» {state}.")
    return redirect("alerts:rules")


@require_POST
def rule_delete(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(AlertRule, pk=pk)
    rule.delete()
    messages.success(request, f"Правило «{rule.name}» удалено, история алертов сохранена.")
    return redirect("alerts:rules")
