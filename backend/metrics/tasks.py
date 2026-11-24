"""Background jobs run by Celery beat (schedule: CELERY_BEAT_SCHEDULE in settings).

Stage 2 adds alert evaluation here, stage 3 — log embedding for the AI analysis.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from . import demo
from .models import LogEntry, MetricSnapshot

logger = logging.getLogger(__name__)


@shared_task
def prune_old_data() -> dict[str, int]:
    """Retention: keeps the database size bounded."""
    now = timezone.now()
    metrics_cutoff = now - timedelta(days=settings.NIGHTWATCH_METRICS_RETENTION_DAYS)
    logs_cutoff = now - timedelta(days=settings.NIGHTWATCH_LOGS_RETENTION_DAYS)

    snapshots, _ = MetricSnapshot.objects.filter(timestamp__lt=metrics_cutoff).delete()
    logs, _ = LogEntry.objects.filter(timestamp__lt=logs_cutoff).delete()
    logger.info("Retention: deleted %d snapshots and %d log entries", snapshots, logs)
    return {"snapshots": snapshots, "logs": logs}


@shared_task
def simulate_demo_servers() -> int:
    """Keeps demo servers online with fresh synthetic reports (NIGHTWATCH_DEMO_MODE=1)."""
    if not settings.NIGHTWATCH_DEMO_MODE:
        return 0
    return demo.tick()
