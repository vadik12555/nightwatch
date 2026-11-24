"""Read-side queries shared by the web UI and the Telegram bot."""

from django.db.models import Count, OuterRef, Q, Subquery

from metrics.models import MetricSnapshot

from .models import Server


def servers_with_latest_metrics() -> list[Server]:
    """All servers with their latest snapshot, failed-service and open-alert counts.

    Two queries in total regardless of the number of servers (no N+1).
    `distinct=True` matters: two COUNTs over different joins would multiply otherwise.
    """
    latest_id = (
        MetricSnapshot.objects.filter(server=OuterRef("pk")).order_by("-timestamp").values("pk")[:1]
    )
    servers = list(
        Server.objects.annotate(
            latest_snapshot_id=Subquery(latest_id),
            failed_services=Count("services", filter=Q(services__status="failed"), distinct=True),
            open_alerts=Count("alerts", filter=Q(alerts__status="open"), distinct=True),
        )
        # Explicit: Meta.ordering is ignored in GROUP BY (aggregate) queries.
        .order_by("name")
    )
    snapshots = MetricSnapshot.objects.in_bulk(
        [s.latest_snapshot_id for s in servers if s.latest_snapshot_id]
    )
    for server in servers:
        server.latest = snapshots.get(server.latest_snapshot_id)
    return servers


def fleet_summary(servers: list[Server]) -> dict[str, int]:
    statuses = [s.status for s in servers]
    return {
        "total": len(servers),
        "online": statuses.count(Server.Status.ONLINE),
        "offline": statuses.count(Server.Status.OFFLINE),
        "pending": statuses.count(Server.Status.PENDING),
        "failed_services": sum(s.failed_services for s in servers),
        "open_alerts": sum(s.open_alerts for s in servers),
    }
