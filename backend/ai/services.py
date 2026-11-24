"""AI use cases: explain an error, index a runbook, answer a chat question.

Each slow operation is split in two: a fast synchronous part that creates a row
with status "pending" (called from the web request), and a `run_*` function that
talks to the LLM (called from a Celery task). The page polls the row until it is ready.
"""

import logging
import re

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from metrics.models import LogEntry
from servers.models import Server

from . import prompts, retrieval
from .chunking import split_markdown
from .context import server_context
from .fingerprint import fingerprint
from .models import ChatMessage, JobStatus, LogExplanation, Runbook, RunbookChunk
from .providers import LLMError, LLMProvider, get_provider

logger = logging.getLogger(__name__)

HISTORY_MESSAGES = 6
EXPLAINABLE_LEVELS = {LogEntry.Level.ERROR, LogEntry.Level.CRITICAL}


# --- «Объяснить» ------------------------------------------------------------------------


def request_explanation(entry: LogEntry) -> tuple[LogExplanation, bool]:
    """Return the cached explanation for this error, or create a pending one.

    The bool says whether the LLM has to be called (new or previously failed).
    """
    explanation, created = LogExplanation.objects.get_or_create(
        fingerprint=fingerprint(entry.source, entry.message),
        defaults={
            "source": entry.source,
            "sample_message": entry.message,
            "log_entry": entry,
        },
    )
    if created:
        return explanation, True
    if explanation.status == JobStatus.FAILED:  # retry after an error
        explanation.status, explanation.error, explanation.log_entry = JobStatus.PENDING, "", entry
        explanation.save(update_fields=["status", "error", "log_entry", "updated_at"])
        return explanation, True
    if explanation.status == JobStatus.READY:
        LogExplanation.objects.filter(pk=explanation.pk).update(hits=F("hits") + 1)
    return explanation, False


def run_explanation(explanation_id: int, provider: LLMProvider | None = None) -> LogExplanation:
    explanation = LogExplanation.objects.select_related("log_entry__server").get(pk=explanation_id)
    entry = explanation.log_entry
    explanation.status = JobStatus.RUNNING
    explanation.save(update_fields=["status", "updated_at"])
    try:
        provider = provider or get_provider()
        hits = retrieval.search(explanation.sample_message, provider, k=3)
        messages = prompts.explain_messages(
            source=explanation.source,
            level=entry.level if entry else "error",
            message=explanation.sample_message,
            server_context=server_context(entry.server) if entry else "",
            hits=hits,
        )
        explanation.explanation = provider.chat(messages)
        explanation.model = provider.model
        explanation.status = JobStatus.READY
    except LLMError as exc:
        logger.warning("Explanation %s failed: %s", explanation_id, exc)
        explanation.status, explanation.error = JobStatus.FAILED, str(exc)
    explanation.save()
    return explanation


# --- Runbooks ------------------------------------------------------------------------------


def create_runbook(*, title: str, content: str, source_name: str = "", user=None) -> Runbook:
    return Runbook.objects.create(
        title=title.strip()[:200] or source_name or "Без названия",
        content=content,
        source_name=source_name,
        uploaded_by=user,
    )


def title_from_markdown(content: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", content, re.M)
    return match.group(1).strip() if match else fallback


def run_indexing(runbook_id: int, provider: LLMProvider | None = None) -> Runbook:
    runbook = Runbook.objects.get(pk=runbook_id)
    runbook.status, runbook.error = JobStatus.RUNNING, ""
    runbook.save(update_fields=["status", "error", "updated_at"])

    chunks = split_markdown(runbook.content)
    try:
        provider = provider or get_provider()
        vectors = provider.embed([chunk.embed_text for chunk in chunks]) if chunks else []
    except LLMError as exc:
        logger.warning("Indexing runbook %s failed: %s", runbook_id, exc)
        runbook.status, runbook.error = JobStatus.FAILED, str(exc)
        runbook.save(update_fields=["status", "error", "updated_at"])
        return runbook

    # Replace chunks atomically: search never sees a half-indexed runbook.
    with transaction.atomic():
        runbook.chunks.all().delete()
        RunbookChunk.objects.bulk_create(
            RunbookChunk(
                runbook=runbook,
                position=position,
                heading=chunk.heading[:300],
                text=chunk.text,
                embedding=vector,
            )
            for position, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        )
        runbook.status, runbook.indexed_at = JobStatus.READY, timezone.now()
        runbook.save(update_fields=["status", "indexed_at", "updated_at"])
    return runbook


# --- Chat ---------------------------------------------------------------------------------


def ask(user, question: str, server: Server | None = None) -> ChatMessage:
    """Store the question and a pending answer; returns the pending assistant message."""
    with transaction.atomic():
        question_msg = ChatMessage.objects.create(
            user=user, server=server, role=ChatMessage.Role.USER, content=question.strip()
        )
        return ChatMessage.objects.create(
            user=user,
            server=server,
            role=ChatMessage.Role.ASSISTANT,
            reply_to=question_msg,
            status=JobStatus.PENDING,
        )


def _history(answer: ChatMessage) -> list[prompts.Message]:
    previous = (
        ChatMessage.objects.filter(
            user=answer.user, status=JobStatus.READY, created_at__lt=answer.reply_to.created_at
        )
        .exclude(content="")
        .order_by("-created_at", "-id")[:HISTORY_MESSAGES]
    )
    return [{"role": m.role, "content": m.content} for m in reversed(previous)]


def run_answer(message_id: int, provider: LLMProvider | None = None) -> ChatMessage:
    answer = ChatMessage.objects.select_related("reply_to", "server").get(pk=message_id)
    question = answer.reply_to.content
    answer.status = JobStatus.RUNNING
    answer.save(update_fields=["status"])
    try:
        provider = provider or get_provider()
        hits = retrieval.search(question, provider)
        context = server_context(answer.server) if answer.server else ""
        messages = prompts.chat_messages(
            question=question, history=_history(answer), hits=hits, server_context=context
        )
        answer.content = provider.chat(messages)
        answer.sources = _sources(hits, answer.server, answer.content)
        answer.status = JobStatus.READY
    except LLMError as exc:
        logger.warning("Chat answer %s failed: %s", message_id, exc)
        answer.status, answer.error = JobStatus.FAILED, str(exc)
    answer.save()
    return answer


def _sources(hits: list[retrieval.Hit], server: Server | None, answer: str) -> list[dict]:
    sources = [
        {
            "n": str(n),
            "type": "runbook",
            "runbook_id": hit.chunk.runbook_id,
            "title": hit.chunk.runbook.title,
            "heading": hit.chunk.heading,
            "distance": round(hit.distance, 3),
            "cited": f"[{n}]" in answer,
        }
        for n, hit in enumerate(hits, start=1)
    ]
    if server:
        sources.append(
            {
                "n": "S",
                "type": "server",
                "server_id": server.pk,
                "title": server.name,
                "heading": "метрики, алерты и логи",
                "cited": "[S]" in answer,
            }
        )
    # Cited sources first: they are what the answer actually relies on.
    return sorted(sources, key=lambda s: not s["cited"])
