from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from metrics.models import LogEntry
from servers.models import Server

from . import services, tasks
from .forms import ChatForm, RunbookUploadForm
from .management.commands.load_runbooks import SAMPLES_DIR
from .models import ChatMessage, JobStatus, LogExplanation, Runbook, WeeklyReport
from .providers import is_configured

HTMX_STOP_POLLING = 286
CHAT_HISTORY_ON_PAGE = 50


def _finished(status: str) -> bool:
    return status in (JobStatus.READY, JobStatus.FAILED)


def _enqueue(task, pk: int) -> None:
    # After commit: the worker must see the row it is asked to process.
    transaction.on_commit(lambda: task.delay(pk))


# --- «Объяснить» ------------------------------------------------------------------------


@require_POST
def explain_log(request: HttpRequest, log_id: int) -> HttpResponse:
    entry = get_object_or_404(LogEntry.objects.select_related("server"), pk=log_id)
    if entry.level not in services.EXPLAINABLE_LEVELS:
        return HttpResponseBadRequest("Объяснять можно только ошибки (ERROR и CRITICAL).")
    explanation, needs_run = services.request_explanation(entry)
    if needs_run:
        _enqueue(tasks.explain_log, explanation.pk)
    return render(
        request,
        "ai/partials/explanation_drawer.html",
        {"explanation": explanation, "entry": entry, "from_cache": not needs_run},
    )


@require_GET
def explanation_status(request: HttpRequest, pk: int) -> HttpResponse:
    explanation = get_object_or_404(LogExplanation, pk=pk)
    response = render(request, "ai/partials/explanation_body.html", {"explanation": explanation})
    if _finished(explanation.status):
        response.status_code = HTMX_STOP_POLLING
    return response


# --- Chat ------------------------------------------------------------------------------------


@require_GET
def chat(request: HttpRequest) -> HttpResponse:
    history = list(
        ChatMessage.objects.filter(user=request.user)
        .select_related("server")
        .order_by("-created_at", "-id")[:CHAT_HISTORY_ON_PAGE]
    )
    selected = request.GET.get("server", "")
    return render(
        request,
        "ai/chat.html",
        {
            "chat_messages": list(reversed(history)),
            "servers": Server.objects.only("pk", "name"),
            "selected_server": int(selected) if selected.isdigit() else None,
            "runbooks_ready": Runbook.objects.filter(status=JobStatus.READY).count(),
            "tab": "chat",
        },
    )


@require_POST
def chat_send(request: HttpRequest) -> HttpResponse:
    form = ChatForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Пустой или слишком длинный вопрос (до 2000 символов).")
    server_id = form.cleaned_data.get("server")
    server = Server.objects.filter(pk=server_id).first() if server_id else None
    answer = services.ask(request.user, form.cleaned_data["question"], server)
    _enqueue(tasks.answer_chat_message, answer.pk)
    return render(
        request,
        "ai/partials/chat_pair.html",
        {"question": answer.reply_to, "answer": answer},
    )


@require_GET
def chat_message(request: HttpRequest, pk: int) -> HttpResponse:
    message = get_object_or_404(ChatMessage, pk=pk, user=request.user)
    response = render(request, "ai/partials/chat_message.html", {"message": message})
    if _finished(message.status):
        response.status_code = HTMX_STOP_POLLING
    return response


@require_POST
def chat_clear(request: HttpRequest) -> HttpResponse:
    ChatMessage.objects.filter(user=request.user).delete()
    return redirect("ai:chat")


# --- Runbooks ---------------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def runbooks(request: HttpRequest) -> HttpResponse:
    form = RunbookUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        created = []
        for name, text in form.cleaned_data["files"]:
            title = services.title_from_markdown(text, name.rsplit(".", 1)[0])
            created.append(
                services.create_runbook(
                    title=title, content=text, source_name=name, user=request.user
                )
            )
        if form.cleaned_data["content"].strip():
            created.append(
                services.create_runbook(
                    title=form.cleaned_data["title"],
                    content=form.cleaned_data["content"],
                    user=request.user,
                )
            )
        for runbook in created:
            _enqueue(tasks.index_runbook, runbook.pk)
        messages.success(request, f"Загружено runbook'ов: {len(created)}. Индексация запущена.")
        return redirect("ai:runbooks")

    return render(
        request,
        "ai/runbooks.html",
        {
            "form": form,
            "runbooks": Runbook.objects.prefetch_related("chunks").order_by("title"),
            "tab": "runbooks",
        },
    )


@require_GET
def runbook_detail(request: HttpRequest, pk: int) -> HttpResponse:
    runbook = get_object_or_404(Runbook, pk=pk)
    return render(
        request,
        "ai/runbook_detail.html",
        {"runbook": runbook, "chunks": runbook.chunks.all(), "tab": "runbooks"},
    )


@require_POST
def runbook_reindex(request: HttpRequest, pk: int) -> HttpResponse:
    runbook = get_object_or_404(Runbook, pk=pk)
    runbook.status = JobStatus.PENDING
    runbook.save(update_fields=["status", "updated_at"])
    _enqueue(tasks.index_runbook, runbook.pk)
    messages.info(request, f"«{runbook.title}» поставлен в очередь на индексацию.")
    return redirect("ai:runbooks")


@require_POST
def runbook_delete(request: HttpRequest, pk: int) -> HttpResponse:
    runbook = get_object_or_404(Runbook, pk=pk)
    runbook.delete()
    messages.success(request, f"Runbook «{runbook.title}» удалён.")
    return redirect("ai:runbooks")


@require_POST
def load_samples(request: HttpRequest) -> HttpResponse:
    count = 0
    for file in sorted(SAMPLES_DIR.glob("*.md")):
        if Runbook.objects.filter(source_name=file.name).exists():
            continue
        content = file.read_text(encoding="utf-8")
        runbook = services.create_runbook(
            title=services.title_from_markdown(content, file.stem),
            content=content,
            source_name=file.name,
            user=request.user,
        )
        _enqueue(tasks.index_runbook, runbook.pk)
        count += 1
    messages.success(request, f"Добавлено примеров: {count}.")
    return redirect("ai:runbooks")


# --- Weekly reports -----------------------------------------------------------------------------


@require_GET
def reports(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "ai/reports.html",
        {"reports": WeeklyReport.objects.all()[:20], "tab": "reports"},
    )


@require_POST
def reports_run(request: HttpRequest) -> HttpResponse:
    transaction.on_commit(lambda: tasks.weekly_health_report.delay())
    note = "" if is_configured() else " AI не настроен — сводка будет из одних цифр."
    messages.info(request, "Сводка формируется и будет отправлена в Telegram." + note)
    return redirect("ai:reports")
