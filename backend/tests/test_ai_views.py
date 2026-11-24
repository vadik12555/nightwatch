import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ai.fingerprint import fingerprint
from ai.models import ChatMessage, JobStatus, LogExplanation, Runbook, WeeklyReport
from metrics.models import LogEntry

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def error_log(server):
    return LogEntry.objects.create(
        server=server,
        timestamp=timezone.now(),
        level="error",
        source="nginx",
        message="upstream timed out (110: Connection timed out) while reading response header",
    )


class TestAccess:
    @pytest.mark.parametrize("name", ["ai:chat", "ai:runbooks", "ai:reports"])
    def test_login_required(self, client, name):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_not_configured_banner(self, auth_client):
        html = auth_client.get(reverse("ai:chat")).content.decode()
        assert "AI не настроен" in html
        assert "LLM_API_KEY" in html


class TestExplainButton:
    def test_button_only_on_errors_and_only_when_enabled(
        self, auth_client, server, error_log, settings
    ):
        LogEntry.objects.create(
            server=server, timestamp=timezone.now(), level="info", source="cron", message="ok"
        )
        url = reverse("servers:logs", args=[server.pk])
        assert "Объяснить" not in auth_client.get(url).content.decode()

        settings.LLM_API_KEY = "key"
        html = auth_client.get(url).content.decode()
        assert html.count("✨ Объяснить") == 1
        assert reverse("ai:explain", args=[error_log.pk]) in html

    def test_explain_flow(
        self, auth_client, error_log, fake_llm, django_capture_on_commit_callbacks
    ):
        fake_llm.reply = "### Что случилось\nТаймаут.\n```bash\nsystemctl status gunicorn\n```"
        url = reverse("ai:explain", args=[error_log.pk])

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(url, **HTMX)
        assert response.status_code == 200
        assert "Модель разбирает ошибку" in response.content.decode()  # first render: pending

        explanation = LogExplanation.objects.get()
        assert explanation.status == JobStatus.READY  # the (eager) Celery task ran after commit
        poll = auth_client.get(reverse("ai:explanation", args=[explanation.pk]), **HTMX)
        assert poll.status_code == 286  # HTMX: stop polling
        html = poll.content.decode()
        assert "<h3>Что случилось</h3>" in html
        assert "systemctl status gunicorn" in html
        assert "Копировать" in html

    def test_second_click_is_served_from_cache(self, auth_client, error_log, fake_llm):
        explanation = LogExplanation.objects.create(
            fingerprint=fingerprint(error_log.source, error_log.message),
            source="nginx",
            sample_message=error_log.message,
            status=JobStatus.READY,
            explanation="готово",
            model="fake-llm",
        )
        html = auth_client.post(reverse("ai:explain", args=[error_log.pk]), **HTMX).content.decode()
        assert "из кэша" in html
        assert fake_llm.chat_calls == []
        explanation.refresh_from_db()
        assert explanation.hits == 1

    def test_only_errors_can_be_explained(self, auth_client, server, fake_llm):
        info = LogEntry.objects.create(
            server=server, timestamp=timezone.now(), level="info", source="cron", message="ok"
        )
        assert auth_client.post(reverse("ai:explain", args=[info.pk])).status_code == 400

    def test_llm_output_is_sanitized(self, auth_client, error_log, fake_llm):
        LogExplanation.objects.create(
            fingerprint=fingerprint(error_log.source, error_log.message),
            source="nginx",
            sample_message=error_log.message,
            status=JobStatus.READY,
            explanation="<script>alert('x')</script> **ok**",
        )
        html = auth_client.post(reverse("ai:explain", args=[error_log.pk])).content.decode()
        assert "<script>alert" not in html
        assert "<strong>ok</strong>" in html


class TestChat:
    def test_send_and_poll(
        self, auth_client, user, server, fake_llm, django_capture_on_commit_callbacks
    ):
        fake_llm.reply = "Проверьте сервер [S]."
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(
                reverse("ai:chat_send"),
                {"question": "Что с сервером?", "server": server.pk},
                **HTMX,
            )
        html = response.content.decode()
        assert "Что с сервером?" in html
        assert "думаю" in html

        answer = ChatMessage.objects.get(role="assistant")
        poll = auth_client.get(reverse("ai:chat_message", args=[answer.pk]), **HTMX)
        assert poll.status_code == 286
        body = poll.content.decode()
        assert "Проверьте сервер [S]." in body
        assert "[S] web-01" in body
        assert "source--cited" in body

        page = auth_client.get(reverse("ai:chat")).content.decode()
        assert "Что с сервером?" in page

    def test_empty_question_rejected(self, auth_client, fake_llm):
        assert auth_client.post(reverse("ai:chat_send"), {"question": "  "}).status_code == 400

    def test_cannot_read_other_users_messages(self, auth_client, django_user_model):
        bob = django_user_model.objects.create_user("bob", password="x")
        message = ChatMessage.objects.create(user=bob, role="assistant", content="secret")
        assert auth_client.get(reverse("ai:chat_message", args=[message.pk])).status_code == 404

    def test_clear_history(self, auth_client, user):
        ChatMessage.objects.create(user=user, role="user", content="hi")
        auth_client.post(reverse("ai:chat_clear"))
        assert not ChatMessage.objects.exists()


class TestRunbooks:
    def test_upload_markdown_files(self, auth_client, fake_llm, django_capture_on_commit_callbacks):
        upload = SimpleUploadedFile("redis.md", "# Redis упал\n\nПерезапустить.".encode())
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(reverse("ai:runbooks"), {"files": [upload]})
        assert response.status_code == 302

        runbook = Runbook.objects.get()
        assert (runbook.title, runbook.source_name) == ("Redis упал", "redis.md")
        assert runbook.status == JobStatus.READY
        assert runbook.chunks.count() == 1

    def test_paste_text_requires_title(self, auth_client, fake_llm):
        response = auth_client.post(reverse("ai:runbooks"), {"content": "# x\n\ny"})
        assert response.status_code == 200
        assert "Укажите название" in response.content.decode()

    def test_rejects_other_file_types(self, auth_client, fake_llm):
        upload = SimpleUploadedFile("evil.exe", b"MZ")
        response = auth_client.post(reverse("ai:runbooks"), {"files": [upload]})
        assert "нужен .md" in response.content.decode()
        assert not Runbook.objects.exists()

    def test_load_samples_detail_reindex_delete(
        self, auth_client, fake_llm, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            auth_client.post(reverse("ai:load_samples"))
        assert Runbook.objects.filter(status=JobStatus.READY).count() == 6

        runbook = Runbook.objects.get(source_name="disk-full.md")
        html = auth_client.get(reverse("ai:runbook", args=[runbook.pk])).content.decode()
        assert "Фрагменты для поиска" in html
        assert "<h2>Диагностика</h2>" in html

        with django_capture_on_commit_callbacks(execute=True):
            auth_client.post(reverse("ai:runbook_reindex", args=[runbook.pk]))
        runbook.refresh_from_db()
        assert runbook.status == JobStatus.READY

        auth_client.post(reverse("ai:runbook_delete", args=[runbook.pk]))
        assert Runbook.objects.count() == 5


class TestReports:
    def test_run_now(self, auth_client, server, fake_llm, django_capture_on_commit_callbacks):
        fake_llm.reply = "Всё спокойно."
        with django_capture_on_commit_callbacks(execute=True):
            auth_client.post(reverse("ai:reports_run"))
        report = WeeklyReport.objects.get()
        assert report.ai_generated

        html = auth_client.get(reverse("ai:reports")).content.decode()
        assert "Всё спокойно." in html
