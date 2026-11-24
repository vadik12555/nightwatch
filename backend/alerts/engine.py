"""Alert evaluation. Celery beat calls `evaluate()` every 30 seconds.

For every server that has ever reported, three kinds of checks run:

* offline   — the agent has been silent longer than NIGHTWATCH_OFFLINE_AFTER (2 min);
* service   — a systemd unit reported by the agent is `failed`;
* metric    — user-defined AlertRule, e.g. «CPU > 90 % for 5 minutes».

Every check yields one of three answers, like Prometheus' `for:` clause:
True (firing), False (healthy) or None (not proven yet — e.g. the condition holds
but not for the whole duration). Firing opens an Alert, healthy closes it, None
keeps the current state. Each transition sends a notification after the DB commit.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Min
from django.utils import timezone

from metrics.models import MetricSnapshot, ServiceStatus
from notifications.tasks import send_alert_notification
from servers.models import Server

from .models import Alert, AlertRule, Severity

logger = logging.getLogger(__name__)

# Agents report every ~15 s, so a reading this close to the window start counts as covering it.
COVERAGE_SLACK = timedelta(seconds=45)

OPENED = "opened"
RESOLVED = "resolved"


@dataclass
class EvaluationResult:
    opened: list[Alert] = field(default_factory=list)
    resolved: list[Alert] = field(default_factory=list)


def notify(alert: Alert, event: str) -> None:
    # on_commit: never notify about an alert whose transaction was rolled back.
    transaction.on_commit(lambda: send_alert_notification.delay(alert.pk, event))


def open_alert(server: Server, key: str, now: datetime, **fields) -> Alert | None:
    try:
        # Savepoint: a duplicate created by a concurrent run must not break the caller.
        with transaction.atomic():
            alert = Alert.objects.create(server=server, key=key, opened_at=now, **fields)
    except IntegrityError:
        logger.info("Alert %s for server %s is already open", key, server.pk)
        return None
    logger.warning("Alert opened: %s — %s", server, alert.message)
    notify(alert, OPENED)
    return alert


def resolve_alert(alert: Alert, now: datetime) -> bool:
    # Conditional UPDATE: only one concurrent evaluator can close (and notify) an alert.
    updated = Alert.objects.filter(pk=alert.pk, status=Alert.Status.OPEN).update(
        status=Alert.Status.RESOLVED, resolved_at=now
    )
    if not updated:
        return False
    alert.status, alert.resolved_at = Alert.Status.RESOLVED, now
    logger.info("Alert resolved: %s — %s", alert.server, alert.message)
    notify(alert, RESOLVED)
    return True


def check_rule(rule: AlertRule, server: Server, now: datetime) -> tuple[bool | None, float | None]:
    """(firing, latest value) for one rule on one server."""
    latest = (
        MetricSnapshot.objects.filter(server=server)
        .order_by("-timestamp")
        .values_list(rule.metric, flat=True)
        .first()
    )
    if latest is None:
        return None, None
    if not rule.matches(latest):
        return False, latest
    if rule.duration == 0:
        return True, latest

    window_start = now - timedelta(seconds=rule.duration)
    # One aggregate query instead of loading every point: "all values > X" ⇔ "min > X".
    stats = MetricSnapshot.objects.filter(
        server=server, timestamp__gte=window_start - COVERAGE_SLACK, timestamp__lte=now
    ).aggregate(first=Min("timestamp"), low=Min(rule.metric), high=Max(rule.metric))
    covered = stats["first"] is not None and stats["first"] <= window_start
    extreme = stats["low"] if rule.condition in ("gt", "gte") else stats["high"]
    if covered and rule.matches(extreme):
        return True, latest
    return None, latest  # holds now, but not for the whole duration yet


def evaluate(now: datetime | None = None) -> EvaluationResult:
    now = now or timezone.now()
    result = EvaluationResult()
    open_alerts = {
        (alert.server_id, alert.key): alert
        for alert in Alert.objects.open().select_related("server", "rule")
    }
    checked: set[tuple[int, str]] = set()
    rules = list(AlertRule.objects.filter(is_enabled=True))
    offline_after = settings.NIGHTWATCH_OFFLINE_AFTER
    offline_minutes = int(offline_after.total_seconds() // 60)

    def apply(server: Server, key: str, firing: bool | None, **fields) -> None:
        checked.add((server.pk, key))
        current = open_alerts.get((server.pk, key))
        if firing and current is None:
            alert = open_alert(server, key, now, **fields)
            if alert:
                result.opened.append(alert)
        elif firing is False and current is not None and resolve_alert(current, now):
            result.resolved.append(current)

    # Servers that never reported (pending) have nothing to evaluate yet.
    for server in Server.objects.exclude(last_seen=None):
        is_offline = now - server.last_seen > offline_after
        apply(
            server,
            "offline",
            is_offline,
            kind=Alert.Kind.OFFLINE,
            severity=Severity.CRITICAL,
            message=f"Агент не выходит на связь больше {offline_minutes} мин",
        )
        if is_offline:
            # Data is stale: freeze the server's other alerts until the agent is back.
            checked.update(key for key in open_alerts if key[0] == server.pk)
            continue

        failed = ServiceStatus.objects.filter(
            server=server, status=ServiceStatus.State.FAILED
        ).values_list("name", flat=True)
        for name in failed:
            apply(
                server,
                f"service:{name}",
                True,
                kind=Alert.Kind.SERVICE,
                severity=Severity.CRITICAL,
                message=f"Сервис {name} в состоянии failed",
            )

        for rule in rules:
            if rule.server_id not in (None, server.pk):
                continue
            firing, value = check_rule(rule, server, now)
            apply(
                server,
                f"rule:{rule.pk}",
                firing,
                kind=Alert.Kind.METRIC,
                rule=rule,
                severity=rule.severity,
                value=value,
                message=f"{rule.name}: {rule.format_value(value or 0)} ({rule.expression})",
            )

    # Whatever was not re-checked has no reason to stay open: the service recovered or
    # disappeared, the rule was deleted, disabled or re-scoped to another server.
    for key, alert in open_alerts.items():
        if key not in checked and resolve_alert(alert, now):
            result.resolved.append(alert)

    return result
