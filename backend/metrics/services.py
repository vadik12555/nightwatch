"""Business logic of report ingestion, kept out of the view so it is reusable and testable
(the demo simulator writes data through the very same function)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from servers.models import Server

from .models import LogEntry, MetricSnapshot, ServiceStatus


@dataclass(frozen=True)
class IngestResult:
    snapshot_id: int
    services_saved: int
    logs_saved: int


@transaction.atomic
def ingest_report(server: Server, report: dict[str, Any]) -> IngestResult:
    """Persist one validated agent report. All-or-nothing thanks to the transaction."""
    received_at = timezone.now()
    measured_at: datetime = report.get("timestamp") or received_at

    snapshot = MetricSnapshot.objects.create(
        server=server, timestamp=measured_at, **report["metrics"]
    )

    services = report.get("services") or []
    if services:
        # A single UPSERT (INSERT ... ON CONFLICT DO UPDATE) instead of N queries.
        ServiceStatus.objects.bulk_create(
            [
                ServiceStatus(
                    server=server,
                    name=item["name"],
                    status=item["status"],
                    updated_at=measured_at,
                )
                for item in services
            ],
            update_conflicts=True,
            unique_fields=["server", "name"],
            update_fields=["status", "updated_at"],
        )

    logs = report.get("logs") or []
    if logs:
        LogEntry.objects.bulk_create(
            LogEntry(
                server=server,
                timestamp=item["timestamp"],
                level=item["level"],
                source=item["source"],
                message=item["message"],
            )
            for item in logs
        )

    # UPDATE instead of save(): touches only these columns and avoids overwriting
    # concurrent edits (e.g. a rename in the UI) with stale in-memory values.
    Server.objects.filter(pk=server.pk).update(
        hostname=report["hostname"],
        os=report.get("os", ""),
        agent_version=report.get("agent_version", ""),
        last_seen=received_at,
        updated_at=received_at,
    )

    return IngestResult(snapshot_id=snapshot.pk, services_saved=len(services), logs_saved=len(logs))
