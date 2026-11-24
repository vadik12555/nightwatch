import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramServerError
from celery import shared_task

from alerts.models import Alert

from . import formatting, telegram
from .models import TelegramChat

logger = logging.getLogger(__name__)

TRANSIENT_ERRORS = (TelegramNetworkError, TelegramServerError)
MAX_RETRIES = 5


@shared_task(bind=True, max_retries=MAX_RETRIES)
def send_alert_notification(self, alert_id: int, event: str, chat_ids: list[int] | None = None):
    """Notify every linked chat that an alert was opened or resolved.

    * Telegram network/5xx errors → the task retries later, only for the chats that failed.
    * The user blocked the bot (403) → the chat link is removed, there is no one to notify.
    """
    if not telegram.is_configured():
        logger.debug("TELEGRAM_BOT_TOKEN not set — skipping notification for alert %s", alert_id)
        return 0
    alert = Alert.objects.select_related("server").filter(pk=alert_id).first()
    if alert is None:  # server deleted in the meantime
        return 0

    chats = TelegramChat.objects.filter(notify=True)
    if chat_ids is not None:
        chats = chats.filter(chat_id__in=chat_ids)
    recipients = list(chats.values_list("chat_id", flat=True))

    results = telegram.send_messages(
        recipients,
        formatting.alert_message(alert, event),
        formatting.server_url(alert.server),
    )

    blocked = [cid for cid, err in results.items() if isinstance(err, TelegramForbiddenError)]
    if blocked:
        TelegramChat.objects.filter(chat_id__in=blocked).delete()
        logger.info("Unlinked %d chat(s) that blocked the bot", len(blocked))

    transient = [cid for cid, err in results.items() if isinstance(err, TRANSIENT_ERRORS)]
    for chat_id, error in results.items():
        if error is not None and chat_id not in blocked:
            logger.warning("Telegram send to %s failed: %s", chat_id, error)
    if transient:
        raise self.retry(
            kwargs={"alert_id": alert_id, "event": event, "chat_ids": transient},
            countdown=15 * 2**self.request.retries,  # 15 s, 30 s, 1 min, ...
        )
    return sum(1 for error in results.values() if error is None)
