from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from metrics.models import LogEntry, MetricSnapshot, ServiceStatus
from servers.models import TOKEN_PREFIX, Server, hash_token

pytestmark = pytest.mark.django_db


class TestServerToken:
    def test_create_with_token_stores_only_hash(self):
        server, token = Server.create_with_token(name="db-01")

        assert token.startswith(TOKEN_PREFIX)
        assert len(token) > 40
        assert server.token_hash == hash_token(token)
        assert token not in server.token_hash
        assert token.startswith(server.token_prefix)

    def test_tokens_are_unique(self):
        _, first = Server.create_with_token(name="a")
        _, second = Server.create_with_token(name="b")
        assert first != second

    def test_lookup_by_token(self, server, token):
        assert Server.objects.by_token(token) == server
        assert Server.objects.by_token("nw_wrong") is None
        assert Server.objects.by_token("") is None

    def test_check_token(self, server, token):
        assert server.check_token(token)
        assert not server.check_token(token + "x")

    def test_rotate_token_invalidates_old_one(self, server, token):
        new_token = server.rotate_token()

        assert new_token != token
        assert Server.objects.by_token(token) is None
        assert Server.objects.by_token(new_token) == server

    def test_token_hash_is_unique_in_db(self, server):
        clone = Server(name="clone", token_hash=server.token_hash, token_prefix="nw_x")
        with pytest.raises(IntegrityError):
            clone.save()


class TestServerStatus:
    def test_pending_until_first_report(self, server):
        assert server.status == Server.Status.PENDING
        assert not server.is_online

    def test_online_when_recently_seen(self, server):
        server.last_seen = timezone.now() - timedelta(seconds=10)
        assert server.status == Server.Status.ONLINE
        assert server.get_status_display() == "Online"

    def test_offline_after_threshold(self, server, settings):
        settings.NIGHTWATCH_OFFLINE_AFTER = timedelta(seconds=60)
        server.last_seen = timezone.now() - timedelta(seconds=61)
        assert server.status == Server.Status.OFFLINE

    def test_str_and_url(self, server):
        assert str(server) == "web-01"
        assert server.get_absolute_url() == f"/servers/{server.pk}/"


class TestMetricModels:
    def test_snapshot_latest_and_cascade_delete(self, server):
        now = timezone.now()
        common = {
            "cpu_percent": 1,
            "memory_percent": 2,
            "memory_used": 1,
            "memory_total": 2,
            "disk_percent": 3,
            "disk_used": 1,
            "disk_total": 2,
        }
        MetricSnapshot.objects.create(server=server, timestamp=now - timedelta(minutes=1), **common)
        newest = MetricSnapshot.objects.create(server=server, timestamp=now, **common)

        assert server.snapshots.latest() == newest
        assert "web-01" in str(newest)

        server.delete()
        assert MetricSnapshot.objects.count() == 0

    def test_service_unique_per_server(self, server):
        ServiceStatus.objects.create(
            server=server, name="nginx", status="active", updated_at=timezone.now()
        )
        with pytest.raises(IntegrityError):
            ServiceStatus.objects.create(
                server=server, name="nginx", status="failed", updated_at=timezone.now()
            )

    def test_log_ordering_newest_first(self, server):
        now = timezone.now()
        old = LogEntry.objects.create(
            server=server, timestamp=now - timedelta(hours=1), level="info", source="a", message="1"
        )
        new = LogEntry.objects.create(
            server=server, timestamp=now, level="error", source="b", message="2"
        )
        assert list(server.logs.all()) == [new, old]
        assert str(new) == "[error] b: 2"
