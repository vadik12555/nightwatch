from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from metrics import demo, tasks
from metrics.models import LogEntry, MetricSnapshot
from servers.models import Server

pytestmark = pytest.mark.django_db


def run_seed(*args):
    out = StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


class TestSeedDemo:
    def test_creates_fleet_with_history(self):
        output = run_seed()

        assert "Created 5 demo servers" in output
        servers = {s.name: s for s in Server.objects.all()}
        assert set(servers) == {p.name for p in demo.PROFILES}
        assert all(s.is_demo for s in servers.values())

        web = servers["prod-web-01"]
        assert web.status == Server.Status.ONLINE
        assert web.snapshots.count() > 1_000
        assert web.logs.count() == 220
        assert web.services.filter(status="active").count() == 5
        oldest = web.snapshots.earliest().timestamp
        assert timezone.now() - oldest > timedelta(days=6, hours=23)

        assert servers["backup-node"].status == Server.Status.OFFLINE
        assert servers["staging-app"].services.get(name="celery-worker").status == "failed"

    def test_generated_metrics_are_valid(self):
        run_seed()
        snapshots = MetricSnapshot.objects.all()
        assert not snapshots.filter(cpu_percent__gt=100).exists()
        assert not snapshots.filter(cpu_percent__lt=0).exists()
        for s in snapshots[:500]:
            assert s.memory_used <= s.memory_total
            assert s.disk_used <= s.disk_total

    def test_is_idempotent_and_reset_recreates(self):
        run_seed()
        assert "already exist" in run_seed()
        assert Server.objects.count() == 5

        first_ids = set(Server.objects.values_list("pk", flat=True))
        assert "Deleted 5" in run_seed("--reset")
        assert Server.objects.count() == 5
        assert first_ids.isdisjoint(Server.objects.values_list("pk", flat=True))

    def test_reset_keeps_real_servers(self):
        real, _ = Server.create_with_token(name="my-real-box")
        run_seed()
        run_seed("--reset")
        assert Server.objects.filter(pk=real.pk).exists()

    def test_if_empty_skips_when_servers_exist(self):
        Server.create_with_token(name="my-real-box")
        assert "skipping" in run_seed("--if-empty")
        assert not Server.objects.filter(is_demo=True).exists()


class TestTasks:
    def test_simulation_disabled_by_default(self, settings):
        run_seed()
        settings.NIGHTWATCH_DEMO_MODE = False
        assert tasks.simulate_demo_servers.delay().get() == 0

    def test_simulation_sends_reports_for_online_demo_servers(self, settings):
        run_seed()
        settings.NIGHTWATCH_DEMO_MODE = True
        backup = Server.objects.get(name="backup-node")
        before = MetricSnapshot.objects.count()

        sent = tasks.simulate_demo_servers.delay().get()

        assert sent == 4  # backup-node stays offline on purpose
        assert MetricSnapshot.objects.count() == before + 4
        backup.refresh_from_db()
        assert backup.status == Server.Status.OFFLINE

    def test_prune_old_data(self, server, settings):
        settings.NIGHTWATCH_METRICS_RETENTION_DAYS = 30
        settings.NIGHTWATCH_LOGS_RETENTION_DAYS = 14
        now = timezone.now()
        common = {
            "cpu_percent": 1,
            "memory_percent": 1,
            "memory_used": 1,
            "memory_total": 1,
            "disk_percent": 1,
            "disk_used": 1,
            "disk_total": 1,
        }
        MetricSnapshot.objects.create(server=server, timestamp=now - timedelta(days=31), **common)
        MetricSnapshot.objects.create(server=server, timestamp=now - timedelta(days=29), **common)
        LogEntry.objects.create(
            server=server, timestamp=now - timedelta(days=15), level="info", source="a", message="x"
        )
        LogEntry.objects.create(server=server, timestamp=now, level="info", source="a", message="y")

        assert tasks.prune_old_data.delay().get() == {"snapshots": 1, "logs": 1}
        assert MetricSnapshot.objects.count() == 1
        assert LogEntry.objects.get().message == "y"
