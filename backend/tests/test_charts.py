from datetime import UTC, datetime, timedelta

import pytest

from metrics.charts import RANGES, build_series
from metrics.models import MetricSnapshot
from servers.templatetags import nightwatch as tags

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)


def snapshot(server, at, cpu):
    return MetricSnapshot.objects.create(
        server=server,
        timestamp=at,
        cpu_percent=cpu,
        memory_percent=10,
        memory_used=1,
        memory_total=2,
        disk_percent=10,
        disk_used=1,
        disk_total=2,
    )


def test_points_are_averaged_per_bucket(server):
    snapshot(server, NOW - timedelta(seconds=20), cpu=10)
    snapshot(server, NOW - timedelta(seconds=5), cpu=30)

    data = build_series(server, "1h", now=NOW)

    assert data["bucket_seconds"] == 60
    assert data["series"]["cpu_percent"][-1] == 20.0


def test_missing_buckets_are_gaps(server):
    snapshot(server, NOW - timedelta(minutes=10), cpu=50)

    series = build_series(server, "1h", now=NOW)["series"]["cpu_percent"]

    assert len(series) == RANGES["1h"].points == 60
    assert series[-11] == 50.0
    assert series.count(None) == 59


def test_old_points_outside_range_are_ignored(server):
    snapshot(server, NOW - timedelta(hours=2), cpu=99)
    data = build_series(server, "1h", now=NOW)
    assert set(data["series"]["cpu_percent"]) == {None}


def test_timestamps_are_aligned_epoch_ms(server):
    data = build_series(server, "7d", now=NOW)
    assert len(data["timestamps"]) == 168
    assert all(ts % 3_600_000 == 0 for ts in data["timestamps"])


@pytest.mark.parametrize(
    ("value", "expected"), [(None, "none"), (10, "ok"), (75, "warn"), (95, "crit")]
)
def test_meter_level(value, expected):
    assert tags.meter_level(value) == expected


def test_formatting_filters():
    assert tags.css_percent(12.345) == "12.3"
    assert tags.css_percent(150) == "100.0"
    assert tags.duration(59) == "0 мин"
    assert tags.duration(3 * 3600 + 120) == "3 ч 2 мин"
    assert tags.duration(2 * 86400 + 5 * 3600) == "2 д 5 ч"
    assert tags.bytes_rate(512) == "512 Б/с"
    assert tags.bytes_rate(1536) == "1.5 КБ/с"
    assert tags.bytes_rate(5 * 1024**2) == "5.0 МБ/с"
    assert tags.ago(None) == "никогда"
