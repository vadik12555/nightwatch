import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

TOKEN_PREFIX = "nw_"  # noqa: S105 - a public marker, not a secret


def hash_token(raw_token: str) -> str:
    """SHA-256 of the token.

    Tokens are 256-bit random strings, so a fast hash is enough (no brute-force
    risk, unlike human passwords that need bcrypt/argon2). The database only ever
    sees the hash: a leaked DB dump does not leak working agent credentials.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


class ServerQuerySet(models.QuerySet):
    def by_token(self, raw_token: str) -> "Server | None":
        if not raw_token:
            return None
        return self.filter(token_hash=hash_token(raw_token)).first()


class Server(models.Model):
    """A monitored machine. Its agent authenticates with a per-server API token."""

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        PENDING = "pending", "Ожидает агента"

    name = models.CharField("название", max_length=100)
    # Filled in by the agent on every report.
    hostname = models.CharField("hostname", max_length=255, blank=True)
    os = models.CharField("ОС", max_length=255, blank=True)
    agent_version = models.CharField("версия агента", max_length=32, blank=True)

    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(
        "начало токена",
        max_length=16,
        editable=False,
        help_text="Первые символы токена — чтобы узнать его, не храня целиком.",
    )

    last_seen = models.DateTimeField("последний отчёт", null=True, blank=True, db_index=True)
    is_demo = models.BooleanField("демо-сервер", default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="кто добавил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField("создан", auto_now_add=True)
    updated_at = models.DateTimeField("изменён", auto_now=True)

    objects = ServerQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "сервер"
        verbose_name_plural = "серверы"

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("servers:detail", args=[self.pk])

    # --- Tokens -----------------------------------------------------------------

    @classmethod
    def create_with_token(cls, *, name: str, **fields) -> tuple["Server", str]:
        """Create a server and return it together with the raw token (shown to the user once)."""
        server = cls(name=name, **fields)
        raw_token = server.set_new_token()
        server.save()
        return server, raw_token

    def set_new_token(self) -> str:
        """Generate a fresh token, store only its hash and return the raw value (unsaved)."""
        raw_token = generate_token()
        self.token_hash = hash_token(raw_token)
        self.token_prefix = raw_token[: len(TOKEN_PREFIX) + 6]
        return raw_token

    def rotate_token(self) -> str:
        """Invalidate the current token immediately and issue a new one."""
        raw_token = self.set_new_token()
        self.save(update_fields=["token_hash", "token_prefix", "updated_at"])
        return raw_token

    def check_token(self, raw_token: str) -> bool:
        return secrets.compare_digest(self.token_hash, hash_token(raw_token))

    # --- Status -----------------------------------------------------------------

    @property
    def status(self) -> str:
        """Derived from last_seen, so it can never go stale or drift from reality."""
        if self.last_seen is None:
            return self.Status.PENDING
        if timezone.now() - self.last_seen > settings.NIGHTWATCH_OFFLINE_AFTER:
            return self.Status.OFFLINE
        return self.Status.ONLINE

    @property
    def is_online(self) -> bool:
        return self.status == self.Status.ONLINE

    def get_status_display(self) -> str:
        return self.Status(self.status).label
