"""Time series for the Chart.js graphs on the server page.

Raw snapshots arrive every ~15 s: a week is ~40k points per server, far more than
a chart can show. Points are averaged into fixed-size time buckets, and empty
buckets become `None`, so Chart.js draws a visible gap when the agent was down
instead of a misleading straight line.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from django.utils import timezone

from servers.models import Server

from .models import MetricSnapshot


@dataclass(frozen=True)
class ChartRange:
    key: str
    label: str
    span: timedelta
    bucket: timedelta

    @property
    def points(self) -> int:
        return int(self.span / self.bucket)


RANGES: dict[str, ChartRange] = {
    r.key: r
    for r in (
        ChartRange("1h", "1 час", timedelta(hours=1), timedelta(minutes=1)),
        ChartRange("24h", "24 часа", timedelta(hours=24), timedelta(minutes=10)),
        ChartRange("7d", "7 дней", timedelta(days=7), timedelta(hours=1)),
    )
}
DEFAULT_RANGE = "1h"

FIELDS = (
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "load_1",
    "load_5",
    "load_15",
    "net_sent_bps",
    "net_recv_bps",
)


def build_series(server: Server, range_key: str, now: datetime | None = None) -> dict[str, Any]:
    chart_range = RANGES[range_key]
    now = now or timezone.now()
    bucket_seconds = int(chart_range.bucket.total_seconds())

    # Bucket boundaries are aligned to the epoch so consecutive refreshes are stable.
    last_bucket = int(now.timestamp()) // bucket_seconds
    first_bucket = last_bucket - chart_range.points + 1
    start = datetime.fromtimestamp(first_bucket * bucket_seconds, tz=UTC)

    rows = (
        MetricSnapshot.objects.filter(server=server, timestamp__gte=start, timestamp__lte=now)
        .order_by("timestamp")
        .values_list("timestamp", *FIELDS)  # tuples, not model instances: much cheaper
    )

    sums: dict[int, list[float]] = defaultdict(lambda: [0.0] * len(FIELDS))
    counts: dict[int, int] = defaultdict(int)
    for timestamp, *values in rows:
        bucket = int(timestamp.timestamp()) // bucket_seconds
        totals = sums[bucket]
        for index, value in enumerate(values):
            totals[index] += value
        counts[bucket] += 1

    buckets = range(first_bucket, last_bucket + 1)
    series = {
        field: [round(sums[b][index] / counts[b], 2) if counts.get(b) else None for b in buckets]
        for index, field in enumerate(FIELDS)
    }
    return {
        "range": chart_range.key,
        "bucket_seconds": bucket_seconds,
        # Epoch milliseconds: the browser formats them in the viewer's own timezone.
        "timestamps": [b * bucket_seconds * 1000 for b in buckets],
        "series": series,
    }
