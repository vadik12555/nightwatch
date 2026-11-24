from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from metrics.api.serializers import MAX_LOG_MESSAGE, MAX_LOGS
from metrics.models import LogEntry, MetricSnapshot, ServiceStatus
from servers.models import Server

pytestmark = pytest.mark.django_db

URL = reverse("api:ingest")


def post(client, report):
    return client.post(URL, report, format="json")


class TestAuthentication:
    def test_missing_header_returns_401_with_challenge(self, api_client, make_report):
        response = post(api_client, make_report())

        assert response.status_code == 401
        assert response["WWW-Authenticate"] == "Token"
        assert MetricSnapshot.objects.count() == 0

    def test_invalid_token_returns_401(self, api_client, server, make_report):
        api_client.credentials(HTTP_AUTHORIZATION="Token nw_definitely-not-valid")
        response = post(api_client, make_report())

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token."

    @pytest.mark.parametrize("header", ["Token", "Token a b", "Bearer something"])
    def test_malformed_or_foreign_scheme_rejected(self, api_client, server, make_report, header):
        api_client.credentials(HTTP_AUTHORIZATION=header)
        response = post(api_client, make_report())
        assert response.status_code == 401

    def test_rotated_token_stops_working(self, agent_client, server, make_report):
        server.rotate_token()
        assert post(agent_client, make_report()).status_code == 401

    def test_logged_in_user_without_token_is_rejected(self, user, make_report):
        client = APIClient()
        client.force_login(user)
        response = post(client, make_report())
        # Browser sessions are not accepted by the ingest endpoint at all.
        assert response.status_code == 401

    def test_get_not_allowed(self, agent_client):
        assert agent_client.get(URL).status_code == 405


class TestIngestPersistence:
    def test_valid_report_is_saved(self, agent_client, server, make_report):
        response = post(agent_client, make_report())

        assert response.status_code == 201, response.json()
        body = response.json()
        assert body["status"] == "ok"
        assert body["server_id"] == server.pk
        assert body["services_saved"] == 2
        assert body["logs_saved"] == 2

        snapshot = MetricSnapshot.objects.get(pk=body["snapshot_id"])
        assert snapshot.server == server
        assert snapshot.cpu_percent == 12.5
        assert snapshot.memory_total == 8_000_000_000
        assert snapshot.uptime_seconds == 86_400

        assert set(server.services.values_list("name", "status")) == {
            ("nginx", "active"),
            ("postgresql", "failed"),
        }
        assert server.logs.filter(level="error", source="nginx").exists()

    def test_server_info_and_last_seen_updated(self, agent_client, server, make_report):
        before = timezone.now()
        post(agent_client, make_report())
        server.refresh_from_db()

        assert server.hostname == "web-01.example.com"
        assert server.os == "Ubuntu 24.04 LTS"
        assert server.agent_version == "0.1.0"
        assert server.last_seen >= before
        assert server.status == Server.Status.ONLINE

    def test_services_are_upserted_not_duplicated(self, agent_client, server, make_report):
        post(agent_client, make_report())
        post(
            agent_client,
            make_report(services=[{"name": "postgresql", "status": "active"}]),
        )

        assert ServiceStatus.objects.filter(server=server).count() == 2
        assert server.services.get(name="postgresql").status == "active"

    def test_buffered_report_keeps_agent_timestamp(self, agent_client, server, make_report):
        measured = timezone.now() - timedelta(minutes=30)
        post(agent_client, make_report(timestamp=measured.isoformat()))

        snapshot = server.snapshots.get()
        assert abs(snapshot.timestamp - measured) < timedelta(seconds=1)

    def test_minimal_report_without_services_and_logs(self, agent_client, server, make_report):
        report = make_report()
        del report["services"], report["logs"], report["timestamp"], report["os"]
        response = post(agent_client, report)

        assert response.status_code == 201
        assert server.snapshots.count() == 1
        assert LogEntry.objects.count() == 0

    def test_long_log_message_is_truncated(self, agent_client, server, make_report):
        report = make_report()
        report["logs"][0]["message"] = "x" * (MAX_LOG_MESSAGE + 500)
        post(agent_client, report)

        saved = server.logs.get(source="nginx").message
        assert len(saved) == MAX_LOG_MESSAGE
        assert saved.endswith("…")

    def test_reports_are_isolated_per_server(self, agent_client, server, make_report):
        other, _ = Server.create_with_token(name="other")
        post(agent_client, make_report())

        assert other.snapshots.count() == 0
        assert server.snapshots.count() == 1


class TestValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("cpu_percent", 150),
            ("cpu_percent", -1),
            ("memory_total", -5),
            ("load_1", "high"),
        ],
    )
    def test_invalid_metric_values(self, agent_client, make_report, field, value):
        report = make_report()
        report["metrics"][field] = value
        response = post(agent_client, report)

        assert response.status_code == 400
        assert field in response.json()["metrics"]
        assert MetricSnapshot.objects.count() == 0

    def test_used_greater_than_total_rejected(self, agent_client, make_report):
        report = make_report()
        report["metrics"]["disk_used"] = report["metrics"]["disk_total"] + 1
        assert post(agent_client, report).status_code == 400

    def test_missing_metrics_rejected(self, agent_client, make_report):
        report = make_report()
        del report["metrics"]
        response = post(agent_client, report)
        assert response.status_code == 400
        assert "metrics" in response.json()

    def test_unknown_log_level_rejected(self, agent_client, make_report):
        report = make_report()
        report["logs"][0]["level"] = "panic"
        assert post(agent_client, report).status_code == 400

    def test_unknown_service_state_rejected(self, agent_client, make_report):
        report = make_report(services=[{"name": "nginx", "status": "exploded"}])
        assert post(agent_client, report).status_code == 400

    def test_duplicate_service_names_rejected(self, agent_client, make_report):
        services = [{"name": "nginx", "status": "active"}, {"name": "nginx", "status": "failed"}]
        assert post(agent_client, make_report(services=services)).status_code == 400

    def test_too_many_logs_rejected(self, agent_client, make_report):
        log = make_report()["logs"][0]
        response = post(agent_client, make_report(logs=[log] * (MAX_LOGS + 1)))
        assert response.status_code == 400

    def test_future_timestamp_rejected(self, agent_client, make_report):
        future = timezone.now() + timedelta(hours=1)
        response = post(agent_client, make_report(timestamp=future.isoformat()))
        assert response.status_code == 400
        assert "timestamp" in response.json()

    def test_non_json_body_rejected(self, agent_client):
        response = agent_client.post(URL, "hostname=x", content_type="text/plain")
        assert response.status_code == 415


class TestRateLimiting:
    def test_requests_over_limit_get_429(self, agent_client, make_report, settings):
        settings.NIGHTWATCH_INGEST_RATE = "2/min"

        assert post(agent_client, make_report()).status_code == 201
        assert post(agent_client, make_report()).status_code == 201
        response = post(agent_client, make_report())

        assert response.status_code == 429
        assert "Retry-After" in response
        assert MetricSnapshot.objects.count() == 2

    def test_limit_is_per_server(self, agent_client, make_report, settings):
        settings.NIGHTWATCH_INGEST_RATE = "1/min"
        _, other_token = Server.create_with_token(name="other")
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION=f"Token {other_token}")

        assert post(agent_client, make_report()).status_code == 201
        assert post(agent_client, make_report()).status_code == 429
        assert post(other_client, make_report()).status_code == 201
