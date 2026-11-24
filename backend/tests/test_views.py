from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from metrics.models import LogEntry, MetricSnapshot, ServiceStatus
from servers.models import Server
from servers.views import HTMX_STOP_POLLING

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def add_snapshot(server, **overrides):
    values = {
        "timestamp": timezone.now(),
        "cpu_percent": 93.0,
        "memory_percent": 50.0,
        "memory_used": 4 * 1024**3,
        "memory_total": 8 * 1024**3,
        "disk_percent": 20.0,
        "disk_used": 20 * 1024**3,
        "disk_total": 100 * 1024**3,
        "load_1": 1.5,
        "uptime_seconds": 90_000,
    }
    values.update(overrides)
    return MetricSnapshot.objects.create(server=server, **values)


class TestAccess:
    @pytest.mark.parametrize("url_name", ["servers:dashboard", "servers:create", "admin:index"])
    def test_pages_require_login(self, client, url_name):
        response = client.get(reverse(url_name))
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_server_pages_require_login(self, client, server):
        for name in ["detail", "install", "stats", "services", "logs", "metrics"]:
            response = client.get(reverse(f"servers:{name}", args=[server.pk]))
            assert response.status_code == 302, name

    def test_login_page_is_public(self, client):
        assert client.get(reverse("accounts:login")).status_code == 200

    def test_login_and_logout(self, client, user):
        response = client.post(
            reverse("accounts:login"), {"username": "alice", "password": "s3cret-pass"}
        )
        assert response.status_code == 302
        assert client.get(reverse("servers:dashboard")).status_code == 200

        client.post(reverse("accounts:logout"))
        assert client.get(reverse("servers:dashboard")).status_code == 302

    def test_healthz_is_public(self, client, db):
        response = client.get(reverse("healthz"))
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestDashboard:
    def test_empty_state(self, auth_client):
        response = auth_client.get(reverse("servers:dashboard"))
        assert response.status_code == 200
        assert "Пока нет ни одного сервера" in response.content.decode()

    def test_cards_show_latest_metrics_and_summary(self, auth_client, server):
        add_snapshot(server, timestamp=timezone.now() - timedelta(minutes=5), cpu_percent=10)
        add_snapshot(server, cpu_percent=93.0)
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now())
        ServiceStatus.objects.create(
            server=server, name="nginx", status="failed", updated_at=timezone.now()
        )
        Server.create_with_token(name="never-reported")

        response = auth_client.get(reverse("servers:dashboard"))
        html = response.content.decode()

        assert response.context["summary"] == {
            "total": 2,
            "online": 1,
            "offline": 0,
            "pending": 1,
            "failed_services": 1,
        }
        assert "web-01" in html
        assert "93%" in html  # latest snapshot, not the older one
        assert "meter__bar--crit" in html
        assert "Упавших сервисов: 1" in html
        assert "Агент ещё не прислал данных" in html

    def test_dashboard_avoids_n_plus_one(self, auth_client, django_assert_max_num_queries):
        for i in range(5):
            s, _ = Server.create_with_token(name=f"srv-{i}")
            add_snapshot(s)
        # session + user + servers + snapshots, independent of the number of servers
        with django_assert_max_num_queries(6):
            auth_client.get(reverse("servers:dashboard"), **HTMX)

    def test_htmx_request_returns_partial(self, auth_client, server):
        response = auth_client.get(reverse("servers:dashboard"), **HTMX)
        html = response.content.decode()
        assert 'id="dashboard-body"' in html
        assert "<html" not in html


class TestAddServer:
    def test_create_shows_token_once(self, auth_client, user):
        response = auth_client.post(reverse("servers:create"), {"name": "  new-box  "})
        server = Server.objects.get(name="new-box")
        assert response["Location"] == reverse("servers:install", args=[server.pk])
        assert server.created_by == user

        page = auth_client.get(response["Location"])
        token = page.context["token"]
        assert token.startswith("nw_")
        assert Server.objects.by_token(token) == server
        html = page.content.decode()
        assert f"--token {token}" in html
        assert "/agent/install.sh | sudo bash" in html

        again = auth_client.get(response["Location"])
        assert again.context["token"] is None
        assert token not in again.content.decode()

    def test_install_command_uses_public_url(self, auth_client, settings):
        settings.NIGHTWATCH_PUBLIC_URL = "https://nw.example.com"
        response = auth_client.post(reverse("servers:create"), {"name": "box"}, follow=True)
        assert response.context["command"].startswith(
            "curl -fsSL https://nw.example.com/agent/install.sh"
        )

    def test_duplicate_name_rejected(self, auth_client, server):
        response = auth_client.post(reverse("servers:create"), {"name": "WEB-01"})
        assert response.status_code == 200
        assert "уже есть" in response.content.decode()
        assert Server.objects.count() == 1

    def test_rotate_token(self, auth_client, server, token):
        response = auth_client.post(reverse("servers:rotate_token", args=[server.pk]), follow=True)
        new_token = response.context["token"]
        assert new_token and new_token != token
        assert Server.objects.by_token(token) is None

    def test_rotate_requires_post(self, auth_client, server):
        assert auth_client.get(reverse("servers:rotate_token", args=[server.pk])).status_code == 405

    def test_install_status_stops_polling_after_first_report(self, auth_client, server):
        url = reverse("servers:install_status", args=[server.pk])
        assert auth_client.get(url, **HTMX).status_code == 200

        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now())
        response = auth_client.get(url, **HTMX)
        assert response.status_code == HTMX_STOP_POLLING
        assert "Агент на связи" in response.content.decode()

    def test_delete_server(self, auth_client, server):
        add_snapshot(server)
        response = auth_client.post(reverse("servers:delete", args=[server.pk]))
        assert response.status_code == 302
        assert not Server.objects.exists()
        assert not MetricSnapshot.objects.exists()


class TestServerPage:
    def test_detail_renders(self, auth_client, server):
        add_snapshot(server)
        response = auth_client.get(reverse("servers:detail", args=[server.pk]))
        assert response.status_code == 200
        html = response.content.decode()
        assert "chart-usage" in html
        assert "25 ч 0 мин" not in html  # uptime is humanized in days
        assert "1 д 1 ч" in html

    def test_unknown_server_404(self, auth_client):
        assert auth_client.get(reverse("servers:detail", args=[999])).status_code == 404

    def test_partials(self, auth_client, server):
        add_snapshot(server)
        ServiceStatus.objects.create(
            server=server, name="nginx", status="active", updated_at=timezone.now()
        )
        for name in ["stats", "services", "status"]:
            response = auth_client.get(reverse(f"servers:{name}", args=[server.pk]), **HTMX)
            assert response.status_code == 200, name
            assert "<html" not in response.content.decode()

    def test_logs_filter_by_min_level_and_text(self, auth_client, server):
        now = timezone.now()
        for level, message in [
            ("debug", "cache warmup"),
            ("info", "request served"),
            ("warning", "slow query 2.3s"),
            ("error", "connection refused"),
            ("critical", "out of memory"),
        ]:
            LogEntry.objects.create(
                server=server, timestamp=now, level=level, source="app", message=message
            )
        url = reverse("servers:logs", args=[server.pk])

        response = auth_client.get(url, {"level": "warning"})
        assert {log.level for log in response.context["logs"]} == {"warning", "error", "critical"}

        response = auth_client.get(url, {"q": "REFUSED"})
        assert [log.message for log in response.context["logs"]] == ["connection refused"]

        response = auth_client.get(url, {"level": "bogus"})
        assert len(response.context["logs"]) == 5

    def test_metrics_json(self, auth_client, server):
        add_snapshot(server, cpu_percent=40.0)
        response = auth_client.get(reverse("servers:metrics", args=[server.pk]), {"range": "24h"})
        data = response.json()

        assert data["range"] == "24h"
        assert len(data["timestamps"]) == len(data["series"]["cpu_percent"]) == 144
        assert data["series"]["cpu_percent"][-1] == 40.0

    def test_metrics_json_rejects_unknown_range(self, auth_client, server):
        response = auth_client.get(reverse("servers:metrics", args=[server.pk]), {"range": "1y"})
        assert response.status_code == 400


class TestAgentFiles:
    @pytest.mark.parametrize(
        "filename",
        ["install.sh", "nightwatch_agent.py", "requirements.txt", "nightwatch-agent.service"],
    )
    def test_agent_files_are_public(self, client, filename):
        response = client.get(reverse("servers:agent_file", args=[filename]))
        assert response.status_code == 200
        assert len(b"".join(response.streaming_content)) > 0

    @pytest.mark.parametrize("filename", ["settings.py", "..%2F.env", ".env"])
    def test_only_whitelisted_files(self, client, filename):
        response = client.get(f"/agent/{filename}")
        assert response.status_code == 404
