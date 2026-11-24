from datetime import timedelta

import pytest
from django.core.cache import cache
from django.db import IntegrityError
from django.utils import timezone

from alerts import engine, tasks
from alerts.models import Alert, AlertRule
from metrics.models import MetricSnapshot, ServiceStatus
from servers.models import Server

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_default_rules(db):
    """Default rules come from a data migration; each test defines its own."""
    AlertRule.objects.all().delete()


@pytest.fixture
def notifications(monkeypatch):
    """Record notifications instead of queueing Celery tasks."""
    sent = []
    monkeypatch.setattr(
        engine.send_alert_notification,
        "delay",
        lambda alert_id, event: sent.append((alert_id, event)),
    )
    return sent


@pytest.fixture
def evaluate(django_capture_on_commit_callbacks):
    """engine.evaluate() that also runs on_commit callbacks (tests live inside a transaction)."""

    def run():
        with django_capture_on_commit_callbacks(execute=True):
            return engine.evaluate()

    return run


@pytest.fixture
def online_server(server):
    Server.objects.filter(pk=server.pk).update(last_seen=timezone.now())
    server.refresh_from_db()
    return server


def series(server, values, *, field="cpu_percent", step=15, end=None):
    """Snapshots every `step` seconds ending at `end`, `field` taking `values` in order."""
    end = end or timezone.now()
    count = len(values)
    MetricSnapshot.objects.bulk_create(
        MetricSnapshot(
            server=server,
            timestamp=end - timedelta(seconds=step * (count - 1 - i)),
            **{
                "cpu_percent": 10,
                "memory_percent": 10,
                "memory_used": 1,
                "memory_total": 2,
                "disk_percent": 10,
                "disk_used": 1,
                "disk_total": 2,
                "load_1": 0.5,
                field: value,
            },
        )
        for i, value in enumerate(values)
    )


def cpu_rule(**overrides):
    fields = {
        "name": "CPU high",
        "metric": "cpu_percent",
        "condition": "gt",
        "threshold": 90,
        "duration": 300,
        "severity": "warning",
    }
    fields.update(overrides)
    return AlertRule.objects.create(**fields)


def open_alerts():
    return list(Alert.objects.open())


class TestOffline:
    def test_silent_agent_opens_offline_alert_once(self, server, notifications, evaluate):
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now() - timedelta(minutes=3))

        result = evaluate()
        evaluate()  # second tick must not duplicate

        [alert] = open_alerts()
        assert result.opened == [alert]
        assert alert.kind == Alert.Kind.OFFLINE
        assert alert.severity == "critical"
        assert "больше 2 мин" in alert.message
        assert notifications == [(alert.pk, "opened")]

    def test_two_minutes_threshold(self, server):
        Server.objects.filter(pk=server.pk).update(
            last_seen=timezone.now() - timedelta(seconds=100)
        )
        engine.evaluate()
        assert open_alerts() == []

    def test_agent_back_resolves_alert(self, server, notifications, evaluate):
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now() - timedelta(minutes=5))
        evaluate()
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now())

        result = evaluate()

        alert = Alert.objects.get()
        assert result.resolved == [alert]
        assert alert.status == Alert.Status.RESOLVED
        assert alert.resolved_at is not None
        assert [event for _, event in notifications] == ["opened", "resolved"]

    def test_pending_server_is_ignored(self, server):
        assert server.last_seen is None
        engine.evaluate()
        assert open_alerts() == []


class TestMetricRules:
    def test_fires_when_condition_holds_for_whole_duration(self, online_server):
        rule = cpu_rule()
        series(online_server, [95] * 25)  # 6 minutes above 90 %

        engine.evaluate()

        [alert] = open_alerts()
        assert alert.rule == rule
        assert alert.kind == Alert.Kind.METRIC
        assert alert.value == 95
        assert alert.message == "CPU high: 95.0% (CPU > 90.0% дольше 5 мин)"

    def test_short_spike_does_not_fire(self, online_server):
        cpu_rule()
        series(online_server, [95] * 8)  # only 2 minutes of data
        engine.evaluate()
        assert open_alerts() == []

    def test_single_dip_in_window_prevents_firing(self, online_server):
        cpu_rule()
        values = [95] * 25
        values[12] = 40
        series(online_server, values)
        engine.evaluate()
        assert open_alerts() == []

    def test_resolves_when_latest_value_recovers(self, online_server, notifications):
        cpu_rule()
        series(online_server, [95] * 25, end=timezone.now() - timedelta(seconds=15))
        engine.evaluate()
        series(online_server, [30])

        engine.evaluate()

        assert open_alerts() == []
        assert Alert.objects.get().status == Alert.Status.RESOLVED

    def test_open_alert_survives_unproven_window(self, online_server):
        cpu_rule()
        series(online_server, [95] * 25)
        engine.evaluate()
        # Older history disappears (e.g. retention): the condition still holds now,
        # it just cannot be proven for the full window — keep the alert open.
        MetricSnapshot.objects.filter(timestamp__lt=timezone.now() - timedelta(minutes=1)).delete()
        engine.evaluate()
        assert len(open_alerts()) == 1

    def test_less_than_condition_and_other_metric(self, online_server):
        AlertRule.objects.create(
            name="Idle", metric="load_1", condition="lt", threshold=0.1, duration=60
        )
        series(online_server, [0.05] * 6, field="load_1")
        engine.evaluate()
        [alert] = open_alerts()
        assert alert.message.startswith("Idle: 0.05")

    def test_zero_duration_fires_immediately(self, online_server):
        cpu_rule(duration=0)
        series(online_server, [99])
        engine.evaluate()
        assert len(open_alerts()) == 1

    def test_rule_scoped_to_another_server(self, online_server):
        other, _ = Server.create_with_token(name="other")
        cpu_rule(server=other, duration=0)
        series(online_server, [99])
        engine.evaluate()
        assert open_alerts() == []

    def test_disabling_or_deleting_rule_resolves_its_alerts(self, online_server):
        rule = cpu_rule(duration=0)
        second = cpu_rule(name="CPU critical", threshold=95, duration=0)
        series(online_server, [99])
        engine.evaluate()
        assert len(open_alerts()) == 2

        rule.is_enabled = False
        rule.save()
        second.delete()
        engine.evaluate()

        assert open_alerts() == []

    def test_offline_server_freezes_metric_alerts(self, online_server):
        cpu_rule(duration=0)
        series(online_server, [99])
        engine.evaluate()
        Server.objects.filter(pk=online_server.pk).update(
            last_seen=timezone.now() - timedelta(minutes=10)
        )

        engine.evaluate()

        kinds = sorted(a.kind for a in open_alerts())
        assert kinds == ["metric", "offline"]


class TestServices:
    def make_service(self, server, name, status):
        return ServiceStatus.objects.update_or_create(
            server=server, name=name, defaults={"status": status, "updated_at": timezone.now()}
        )[0]

    def test_failed_service_alert_lifecycle(self, online_server):
        self.make_service(online_server, "nginx", "failed")
        self.make_service(online_server, "ssh", "active")

        engine.evaluate()
        [alert] = open_alerts()
        assert alert.key == "service:nginx"
        assert alert.message == "Сервис nginx в состоянии failed"

        self.make_service(online_server, "nginx", "active")
        engine.evaluate()
        assert open_alerts() == []

    def test_removed_service_resolves_alert(self, online_server):
        service = self.make_service(online_server, "worker", "failed")
        engine.evaluate()
        service.delete()
        engine.evaluate()
        assert open_alerts() == []


class TestConsistency:
    def test_only_one_open_alert_per_key(self, server):
        Alert.objects.create(server=server, kind="offline", key="offline", severity="critical")
        with pytest.raises(IntegrityError):
            Alert.objects.create(server=server, kind="offline", key="offline", severity="critical")

    def test_resolved_alerts_do_not_block_new_ones(self, server):
        Alert.objects.create(
            server=server, kind="offline", key="offline", severity="critical", status="resolved"
        )
        Alert.objects.create(server=server, kind="offline", key="offline", severity="critical")
        assert Alert.objects.count() == 2

    def test_notification_is_sent_only_after_commit(
        self, server, django_capture_on_commit_callbacks, notifications
    ):
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now() - timedelta(minutes=3))
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            from django.db import transaction

            with transaction.atomic():
                engine.evaluate()
            assert notifications == []
        assert len(callbacks) == 1


class TestTask:
    def test_task_returns_counts(self, server):
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now() - timedelta(minutes=3))
        assert tasks.evaluate_alerts.delay().get() == {"opened": 1, "resolved": 0}

    def test_overlapping_run_is_skipped(self, server):
        cache.add(tasks.LOCK_KEY, "1", timeout=60)
        assert tasks.evaluate_alerts.delay().get() is None
        cache.delete(tasks.LOCK_KEY)
