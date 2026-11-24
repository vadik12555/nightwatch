from django.conf import settings
from django.db import models
from pgvector.django import VectorField

from metrics.models import LogEntry
from servers.models import Server

# Fixed by the database schema (vector(1024)). bge-m3 produces 1024 dimensions natively,
# OpenAI text-embedding-3-* are asked for 1024 via the `dimensions` parameter.
EMBEDDING_DIMENSIONS = 1024


class JobStatus(models.TextChoices):
    PENDING = "pending", "В очереди"
    RUNNING = "running", "Обрабатывается"
    READY = "ready", "Готово"
    FAILED = "failed", "Ошибка"


class Runbook(models.Model):
    """A Markdown instruction of the team («что делать, если…»), indexed for RAG."""

    title = models.CharField("название", max_length=200)
    source_name = models.CharField("файл", max_length=255, blank=True)
    content = models.TextField("текст (Markdown)")
    status = models.CharField(
        "индексация", max_length=10, choices=JobStatus.choices, default=JobStatus.PENDING
    )
    error = models.TextField("ошибка", blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField("загружен", auto_now_add=True)
    updated_at = models.DateTimeField("изменён", auto_now=True)
    indexed_at = models.DateTimeField("проиндексирован", null=True, blank=True)

    class Meta:
        ordering = ["title"]
        verbose_name = "runbook"
        verbose_name_plural = "runbook'и"

    def __str__(self) -> str:
        return self.title


class RunbookChunk(models.Model):
    """A piece of a runbook (one section or part of it) with its embedding."""

    runbook = models.ForeignKey(Runbook, on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()
    heading = models.CharField("раздел", max_length=300, blank=True)
    text = models.TextField()
    # HNSW index (cosine) is created by a migration on PostgreSQL only.
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS)

    class Meta:
        ordering = ["runbook", "position"]
        verbose_name = "фрагмент runbook'а"
        verbose_name_plural = "фрагменты runbook'ов"

    def __str__(self) -> str:
        return f"{self.runbook} #{self.position}: {self.heading}"


class LogExplanation(models.Model):
    """Cached AI explanation of an error, shared by all log lines with the same fingerprint."""

    fingerprint = models.CharField(max_length=64, unique=True)
    source = models.CharField("источник", max_length=255)
    sample_message = models.TextField("пример сообщения")
    log_entry = models.ForeignKey(
        LogEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=10, choices=JobStatus.choices, default=JobStatus.PENDING)
    explanation = models.TextField("объяснение (Markdown)", blank=True)
    error = models.TextField(blank=True)
    model = models.CharField("модель", max_length=100, blank=True)
    hits = models.PositiveIntegerField("отдано из кэша", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI-объяснение ошибки"
        verbose_name_plural = "AI-объяснения ошибок"

    def __str__(self) -> str:
        return f"{self.source}: {self.sample_message[:60]}"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Пользователь"
        ASSISTANT = "assistant", "Ассистент"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_messages"
    )
    server = models.ForeignKey(
        Server, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    reply_to = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="reply"
    )
    content = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=JobStatus.choices, default=JobStatus.READY)
    # [{"n": "1", "type": "runbook", "runbook_id": .., "title": .., "cited": true}, ...]
    sources = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "сообщение AI-чата"
        verbose_name_plural = "сообщения AI-чата"

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:60]}"


class WeeklyReport(models.Model):
    period_start = models.DateTimeField("с")
    period_end = models.DateTimeField("по")
    facts = models.JSONField("факты", default=dict)
    summary = models.TextField("сводка")
    ai_generated = models.BooleanField("написана AI", default=False)
    sent_to = models.PositiveIntegerField("отправлено в чатов", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "еженедельная сводка"
        verbose_name_plural = "еженедельные сводки"

    def __str__(self) -> str:
        return f"Сводка {self.period_start:%d.%m}–{self.period_end:%d.%m}"
