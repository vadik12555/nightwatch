from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from servers.views import is_htmx

from .models import TelegramChat, TelegramLinkCode

SESSION_CODE_KEY = "nightwatch_telegram_code_id"


def _active_code(request: HttpRequest) -> TelegramLinkCode | None:
    code_id = request.session.get(SESSION_CODE_KEY)
    if not code_id:
        return None
    code = TelegramLinkCode.objects.filter(pk=code_id, user=request.user).first()
    return code if code and code.is_valid else None


@require_GET
def telegram_settings(request: HttpRequest) -> HttpResponse:
    code = _active_code(request)
    username = settings.TELEGRAM_BOT_USERNAME
    context = {
        "configured": bool(settings.TELEGRAM_BOT_TOKEN),
        "bot_username": username,
        "code": code,
        "deep_link": f"https://t.me/{username}?start={code.code}" if code and username else "",
        "chats": request.user.telegram_chats.all(),
    }
    template = (
        "notifications/partials/chats.html" if is_htmx(request) else "notifications/telegram.html"
    )
    return render(request, template, context)


@require_POST
def issue_link_code(request: HttpRequest) -> HttpResponse:
    code = TelegramLinkCode.issue(request.user)
    request.session[SESSION_CODE_KEY] = code.pk
    return redirect("notifications:telegram")


@require_POST
def toggle_chat(request: HttpRequest, pk: int) -> HttpResponse:
    # Scoped to request.user: nobody can change someone else's chat by guessing ids.
    chat = get_object_or_404(TelegramChat, pk=pk, user=request.user)
    chat.notify = not chat.notify
    chat.save(update_fields=["notify"])
    return redirect("notifications:telegram")


@require_POST
def unlink_chat(request: HttpRequest, pk: int) -> HttpResponse:
    chat = get_object_or_404(TelegramChat, pk=pk, user=request.user)
    chat.delete()
    messages.success(request, "Чат отвязан.")
    return redirect("notifications:telegram")
