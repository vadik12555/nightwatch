import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

LINK_CODE_TTL = timedelta(minutes=10)


class TelegramChat(models.Model):
    """A Telegram chat linked to a NightWatch account: receives alerts, may run bot commands."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="пользователь",
        on_delete=models.CASCADE,
        related_name="telegram_chats",
    )
    chat_id = models.BigIntegerField("chat id", unique=True)
    username = models.CharField("username", max_length=64, blank=True)
    title = models.CharField("имя", max_length=255, blank=True)
    notify = models.BooleanField("получать уведомления", default=True)
    linked_at = models.DateTimeField("привязан", auto_now_add=True)

    class Meta:
        ordering = ["linked_at"]
        verbose_name = "Telegram-чат"
        verbose_name_plural = "Telegram-чаты"

    def __str__(self) -> str:
        who = f"@{self.username}" if self.username else self.title or self.chat_id
        return f"{who} → {self.user}"


class TelegramLinkCode(models.Model):
    """One-time code that proves a Telegram chat belongs to a logged-in web user.

    Flow: the user presses «Привязать» in the web UI → gets a code / deep link →
    sends `/start <code>` to the bot → the bot links its chat_id to that user.
    The code is single-use and expires in 10 minutes.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_link_codes"
    )
    code = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "код привязки Telegram"
        verbose_name_plural = "коды привязки Telegram"

    def __str__(self) -> str:
        return f"{self.user}: {self.code[:4]}…"

    @classmethod
    def issue(cls, user) -> "TelegramLinkCode":
        """Create a fresh code; any previous unused codes of the user stop working."""
        now = timezone.now()
        cls.objects.filter(user=user, used_at__isnull=True).update(expires_at=now)
        return cls.objects.create(
            user=user,
            # URL-safe: allowed in t.me/<bot>?start=<code> deep links.
            code=secrets.token_urlsafe(12),
            expires_at=now + LINK_CODE_TTL,
        )

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and timezone.now() < self.expires_at
