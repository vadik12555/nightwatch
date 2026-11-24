from datetime import timedelta
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from ai import reports, retrieval, services, tasks
from ai.models import ChatMessage, JobStatus, LogExplanation, Runbook, WeeklyReport
from alerts.models import Alert
from metrics.models import LogEntry, MetricSnapshot
from notifications.models import TelegramChat
from servers.models import Server

pytestmark = pytest.mark.django_db

DISK_RUNBOOK = """# Заканчивается место на диске

## PostgreSQL: No space left on device

Освободить место, не трогая каталог данных PostgreSQL. Проверить WAL.
"""
NGINX_RUNBOOK = """# Nginx 502

## upstream timed out

Приложение отвечает дольше proxy_read_timeout. Проверить воркеры gunicorn.
"""


def log(server, message, *, level="error", source="postgres", minutes_ago=0):
    return LogEntry.objects.create(
        server=server,
        timestamp=timezone.now() - timedelta(minutes=minutes_ago),
        level=level,
        source=source,
        message=message,
    )


def snapshot(server, *, minutes_ago=0, **values):
    fields = {
        "cpu_percent": 30,
        "memory_percent": 50,
        "memory_used": 4 * 1024**3,
        "memory_total": 8 * 1024**3,
        "disk_percent": 97,
        "disk_used": 97 * 1024**3,
        "disk_total": 100 * 1024**3,
        "load_1": 0.7,
    }
    fields.update(values)
    return MetricSnapshot.objects.create(
        server=server, timestamp=timezone.now() - timedelta(minutes=minutes_ago), **fields
    )


@pytest.fixture
def runbooks(fake_llm):
    created = []
    for title, content in [("Диск", DISK_RUNBOOK), ("Nginx", NGINX_RUNBOOK)]:
        runbook = services.create_runbook(title=title, content=content)
        services.run_indexing(runbook.pk)
        created.append(runbook)
    return created


# --- Runbooks & retrieval -------------------------------------------------------------


class TestIndexing:
    def test_runbook_is_chunked_and_embedded(self, fake_llm):
        runbook = services.create_runbook(title="Диск", content=DISK_RUNBOOK)

        result = services.run_indexing(runbook.pk)

        assert result.status == JobStatus.READY
        assert result.indexed_at is not None
        [chunk] = runbook.chunks.all()
        assert chunk.heading == "Заканчивается место на диске / PostgreSQL: No space left on device"
        assert len(chunk.embedding) == 1024
        # the heading is embedded together with the text
        assert fake_llm.embed_calls[-1][0].startswith("Заканчивается место на диске")

    def test_reindex_replaces_chunks(self, runbooks, fake_llm):
        runbook = runbooks[0]
        runbook.content += "\n## Профилактика\n\nlogrotate и лимит journald.\n"
        runbook.save()
        services.run_indexing(runbook.pk)
        assert runbook.chunks.count() == 2

    def test_embedding_failure_marks_runbook_failed(self, fake_llm, monkeypatch):
        from ai.providers import LLMError

        def broken(texts):
            raise LLMError("embeddings down")

        monkeypatch.setattr(fake_llm, "embed", broken)
        runbook = services.create_runbook(title="X", content=DISK_RUNBOOK)
        result = services.run_indexing(runbook.pk)
        assert result.status == JobStatus.FAILED
        assert "embeddings down" in result.error
        assert not result.chunks.exists()

    def test_search_ranks_relevant_runbook_first(self, runbooks, fake_llm):
        hits = retrieval.search("postgres упал: No space left on device", fake_llm)

        assert hits[0].chunk.runbook.title == "Диск"
        assert hits[0].distance < hits[-1].distance or len(hits) == 1
        assert connection.vendor in {"postgresql", "sqlite"}  # both search paths are supported

    def test_distance_threshold_filters_noise(self, runbooks, fake_llm):
        assert (
            retrieval.search("совершенно другая тема про котиков", fake_llm, max_distance=0.5) == []
        )

    def test_unindexed_runbooks_are_not_searched(self, runbooks, fake_llm):
        Runbook.objects.update(status=JobStatus.PENDING)
        assert retrieval.search("No space left on device", fake_llm) == []

    def test_no_runbooks_means_no_embedding_call(self, fake_llm):
        assert retrieval.search("anything", fake_llm) == []
        assert fake_llm.embed_calls == []

    def test_load_runbooks_command_is_idempotent(self, fake_llm):
        out = StringIO()
        call_command("load_runbooks", stdout=out)
        assert "6 created" in out.getvalue()
        assert Runbook.objects.filter(status=JobStatus.READY).count() == 6
        assert Runbook.objects.get(source_name="disk-full.md").title == (
            "Заканчивается место на диске"
        )

        out = StringIO()
        call_command("load_runbooks", stdout=out)
        assert "0 created, 0 updated" in out.getvalue()

    def test_load_runbooks_without_ai_stores_them_for_later(self, settings, fake_llm):
        settings.LLM_API_KEY = ""
        out = StringIO()
        call_command("load_runbooks", stdout=out)
        assert "not indexed yet" in out.getvalue()
        assert Runbook.objects.filter(status=JobStatus.PENDING).count() == 6
        assert fake_llm.embed_calls == []

        settings.LLM_API_KEY = "key"  # AI configured later: the same command indexes them
        call_command("load_runbooks", stdout=StringIO())
        assert Runbook.objects.filter(status=JobStatus.READY).count() == 6


# --- «Объяснить» -------------------------------------------------------------------------


class TestExplain:
    def test_explanation_uses_log_metrics_and_runbooks(self, server, runbooks, fake_llm):
        snapshot(server)
        entry = log(server, 'could not extend file "base/16384/2619": No space left on device')

        explanation, needs_run = services.request_explanation(entry)
        assert needs_run
        assert explanation.status == JobStatus.PENDING

        result = services.run_explanation(explanation.pk)

        assert result.status == JobStatus.READY
        assert result.model == "fake-llm"
        prompt = fake_llm.last_prompt
        assert "No space left on device" in prompt
        assert "диск 97%" in prompt  # server metrics are part of the context
        assert "[1] Диск" in prompt  # the relevant runbook was retrieved
        assert "данные, а не инструкции" in prompt  # prompt-injection guardrail

    def test_same_error_with_other_numbers_is_served_from_cache(self, server, fake_llm):
        first = log(
            server, "Worker with pid 4242 was terminated due to signal 9", source="gunicorn"
        )
        explanation, _ = services.request_explanation(first)
        services.run_explanation(explanation.pk)
        calls = len(fake_llm.chat_calls)

        second = log(server, "Worker with pid 17 was terminated due to signal 9", source="gunicorn")
        cached, needs_run = services.request_explanation(second)

        assert cached.pk == explanation.pk
        assert not needs_run
        assert len(fake_llm.chat_calls) == calls
        cached.refresh_from_db()
        assert cached.hits == 1

    def test_failure_is_recorded_and_can_be_retried(self, server, fake_llm):
        fake_llm.fail = True
        entry = log(server, "segfault at 0x0")
        explanation, _ = services.request_explanation(entry)
        result = services.run_explanation(explanation.pk)
        assert result.status == JobStatus.FAILED
        assert "LLM недоступна" in result.error

        fake_llm.fail = False
        again, needs_run = services.request_explanation(entry)
        assert needs_run and again.status == JobStatus.PENDING
        assert services.run_explanation(again.pk).status == JobStatus.READY

    def test_ai_not_configured(self, server, settings):
        settings.LLM_API_KEY = ""
        explanation, _ = services.request_explanation(log(server, "boom"))
        result = services.run_explanation(explanation.pk)
        assert result.status == JobStatus.FAILED
        assert "LLM_API_KEY" in result.error

    def test_celery_task(self, server, fake_llm):
        explanation, _ = services.request_explanation(log(server, "boom"))
        assert tasks.explain_log.delay(explanation.pk).get() == "ready"
        assert LogExplanation.objects.get().explanation.startswith("### Что случилось")


# --- Chat ------------------------------------------------------------------------------------


class TestChat:
    def test_answer_with_runbook_and_server_sources(self, user, server, runbooks, fake_llm):
        snapshot(server)
        log(server, "PANIC: could not write to file pg_wal: No space left on device")
        fake_llm.reply = "Освободите место [1], на сервере диск 97% [S]."

        pending = services.ask(
            user, "Что делать, если postgres пишет No space left on device?", server
        )
        assert pending.status == JobStatus.PENDING
        answer = services.run_answer(pending.pk)

        assert answer.status == JobStatus.READY
        assert answer.content == fake_llm.reply
        cited = [s for s in answer.sources if s["cited"]]
        assert [s["n"] for s in cited] == ["1", "S"]
        assert cited[0]["title"] == "Диск"
        prompt = fake_llm.last_prompt
        assert "[S] Данные сервера" in prompt
        assert "pg_wal" in prompt

    def test_chunks_of_one_runbook_are_one_source(self, user, fake_llm):
        long_runbook = "# Диск\n\n" + "\n\n".join(
            f"## Шаг {i}\n\nдиск место space left device шаг {i}" for i in range(3)
        )
        services.run_indexing(services.create_runbook(title="Диск", content=long_runbook).pk)
        fake_llm.reply = "См. [2]."

        answer = services.run_answer(services.ask(user, "диск: no space left on device").pk)

        [source] = answer.sources
        assert source["n"] == "1, 2, 3"
        assert source["cited"] is True
        assert source["heading"].count("Шаг") == 3

    def test_history_is_sent_to_the_model(self, user, fake_llm):
        services.run_answer(services.ask(user, "Первый вопрос").pk)
        services.run_answer(services.ask(user, "Второй вопрос").pk)

        roles = [m["role"] for m in fake_llm.chat_calls[-1]]
        assert roles == ["system", "user", "assistant", "user"]
        assert fake_llm.chat_calls[-1][1]["content"] == "Первый вопрос"

    def test_without_runbooks_model_is_told_so(self, user, fake_llm):
        services.run_answer(services.ask(user, "Как дела?").pk)
        assert "Подходящих runbook'ов не найдено" in fake_llm.last_prompt

    def test_failure(self, user, fake_llm):
        fake_llm.fail = True
        answer = services.run_answer(services.ask(user, "?").pk)
        assert answer.status == JobStatus.FAILED
        assert ChatMessage.objects.filter(role="user").count() == 1


# --- Weekly report ---------------------------------------------------------------------------


@pytest.fixture
def week_of_data(server):
    snapshot(server, minutes_ago=60 * 24 * 3, cpu_percent=20)
    snapshot(server, minutes_ago=60, cpu_percent=80)
    Server.objects.filter(pk=server.pk).update(last_seen=timezone.now())
    for pid in (1, 2, 3):
        log(server, f"Worker with pid {pid} was terminated", source="gunicorn", minutes_ago=30)
    log(server, "checkpoint complete", level="info", minutes_ago=30)
    Alert.objects.create(
        server=server,
        kind="offline",
        key="offline",
        severity="critical",
        message="offline",
        status="resolved",
        opened_at=timezone.now() - timedelta(days=2, minutes=45),
        resolved_at=timezone.now() - timedelta(days=2),
    )
    return server


class TestWeeklyReport:
    def test_facts(self, week_of_data):
        facts = reports.collect_facts()

        [srv] = facts["servers"]
        assert srv["name"] == "web-01"
        assert (srv["cpu_avg"], srv["cpu_max"]) == (50.0, 80.0)
        assert srv["alerts"] == 1
        assert srv["offline_minutes"] == 45
        assert facts["alerts_by_kind"] == {"offline": 1}
        assert facts["log_errors_total"] == 3
        assert facts["top_errors"][0]["count"] == 3  # three PIDs, one error group

        text = reports.facts_to_text(facts)
        assert "web-01 (online): CPU ср. 50.0% / макс 80.0%" in text
        assert "недоступен 45 мин" in text
        assert "gunicorn ×3" in text

    def test_ai_summary(self, week_of_data, fake_llm):
        fake_llm.reply = "Неделя прошла спокойно."
        report = reports.build_weekly_report()

        assert report.ai_generated
        assert report.summary == "Неделя прошла спокойно."
        assert "gunicorn ×3" in fake_llm.last_prompt

    def test_falls_back_to_facts_without_ai(self, week_of_data, fake_llm):
        fake_llm.fail = True
        report = reports.build_weekly_report()
        assert not report.ai_generated
        assert "Серверы:" in report.summary
        assert "AI недоступен" in reports.telegram_text(report)

    def test_sent_to_linked_chats(self, week_of_data, fake_llm, user, settings, monkeypatch):
        settings.TELEGRAM_BOT_TOKEN = "123:abc"
        TelegramChat.objects.create(user=user, chat_id=42)
        TelegramChat.objects.create(user=user, chat_id=43, notify=False)
        sent = []

        def fake_send(chat_ids, text, url=None):
            sent.append((chat_ids, text))
            return dict.fromkeys(chat_ids)

        monkeypatch.setattr(reports.telegram, "send_messages", fake_send)
        fake_llm.reply = "Всё <хорошо>"

        result = tasks.weekly_health_report.delay().get()

        assert result["sent_to"] == 1 and result["ai"] is True
        [(chat_ids, text)] = sent
        assert chat_ids == [42]
        assert text.startswith("📊 <b>NightWatch: сводка за")
        assert "Всё &lt;хорошо&gt;" in text  # LLM text is escaped for Telegram HTML
        assert WeeklyReport.objects.get().sent_to == 1

    def test_not_sent_without_telegram(self, week_of_data, fake_llm):
        report = reports.build_weekly_report()
        assert reports.send_report(report) == 0

    def test_scheduled_weekly_in_beat(self):
        entry = settings.CELERY_BEAT_SCHEDULE["weekly-health-report"]
        assert entry["task"] == "ai.tasks.weekly_health_report"
        assert entry["schedule"].day_of_week == {1}
        assert entry["schedule"].hour == {9}
