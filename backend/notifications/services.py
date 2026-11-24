"""Synchronous business logic behind the bot commands.

The aiogram handlers are thin async wrappers around these functions
(via asgiref's sync_to_async), so all the logic is testable without Telegram.
"""

from html import escape

from django.db import transaction
from django.utils import timezone

from alerts.models import Alert, Severity
from servers.selectors import fleet_summary, servers_with_latest_metrics

from . import formatting
from .models import TelegramChat, TelegramLinkCode


def link_chat(code: str, chat_id: int, username: str = "", title: str = "") -> str:
    """`/start <code>`: bind the chat to the user who generated the code. Returns the reply."""
    with transaction.atomic():
        # Row lock: the same code cannot be redeemed twice by parallel requests.
        link_code = (
            TelegramLinkCode.objects.select_for_update()
            .select_related("user")
            .filter(code=code)
            .first()
        )
        if link_code is None or not link_code.is_valid:
            return (
                "Код не подошёл: он неверный, уже использован или истёк (живёт 10 минут).\n"
                "Получите новый на странице <b>Telegram</b> в NightWatch."
            )
        link_code.used_at = timezone.now()
        link_code.save(update_fields=["used_at"])
        TelegramChat.objects.update_or_create(
            chat_id=chat_id,
            defaults={"user": link_code.user, "username": username or "", "title": title or ""},
        )
    return (
        f"✅ Чат привязан к аккаунту <b>{escape(link_code.user.get_username())}</b>.\n"
        "Сюда будут приходить алерты и уведомления о восстановлении.\n\n" + formatting.HELP
    )


def unlink_chat(chat_id: int) -> str:
    deleted, _ = TelegramChat.objects.filter(chat_id=chat_id).delete()
    if deleted:
        return "Чат отвязан. Уведомления больше не придут."
    return "Этот чат и так не привязан."


def is_linked(chat_id: int) -> bool:
    return TelegramChat.objects.filter(chat_id=chat_id).exists()


def start_without_code(chat_id: int) -> str:
    if is_linked(chat_id):
        return "Чат уже привязан к NightWatch.\n\n" + formatting.HELP
    return formatting.NOT_LINKED


def help_text(chat_id: int) -> str:
    return formatting.HELP if is_linked(chat_id) else formatting.NOT_LINKED


def status_text(chat_id: int) -> str:
    # Only linked chats may read infrastructure data: anyone can find a bot by its name.
    if not is_linked(chat_id):
        return formatting.NOT_LINKED
    servers = servers_with_latest_metrics()
    open_alerts = sorted(
        Alert.objects.open().select_related("server"),
        key=lambda a: (a.severity != Severity.CRITICAL, a.opened_at),
    )
    return formatting.status_text(servers, fleet_summary(servers), open_alerts)


def servers_text(chat_id: int) -> str:
    if not is_linked(chat_id):
        return formatting.NOT_LINKED
    return formatting.servers_text(servers_with_latest_metrics())
