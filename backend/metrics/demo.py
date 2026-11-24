"""Synthetic but plausible demo data: a small fleet with daily load cycles, a memory leak,
a growing disk, a failed service, an offline box and realistic log lines.

Used by `manage.py seed_demo` (history) and by the Celery task that keeps demo
servers "alive" (fresh reports every 15 s, written through the real ingest code).
"""

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from servers.models import Server

from .models import LogEntry, MetricSnapshot
from .services import ingest_report

GIB = 1024**3


@dataclass(frozen=True)
class DemoProfile:
    name: str
    hostname: str
    os: str
    cores: int
    memory_total: int
    disk_total: int
    cpu_base: float  # average CPU % at night
    cpu_daily: float  # extra CPU % at the daily peak
    memory_base: float  # %
    disk_start: float  # % a week ago
    disk_growth_per_day: float  # % per day
    net_scale: float  # bytes/s at 100% CPU
    services: dict[str, str]
    log_templates: list[tuple[str, str, str]]  # (level, source, message)
    offline_for: timedelta | None = None
    boot_days_ago: float = 23.4
    extra: dict = field(default_factory=dict)


COMMON_LOGS = [
    ("info", "sshd", "Accepted publickey for deploy from {ip} port {port} ssh2: ED25519"),
    ("warning", "sshd", "Invalid user {user} from {ip} port {port}"),
    ("warning", "sshd", "Failed password for invalid user {user} from {ip} port {port} ssh2"),
    ("info", "systemd", "Started session-{n}.scope - Session {n} of User deploy."),
    ("info", "CRON", "(root) CMD (/usr/local/bin/backup-check.sh > /dev/null 2>&1)"),
    ("info", "systemd", "Starting apt-daily.service - Daily apt download activities..."),
    (
        "warning",
        "kernel",
        "TCP: request_sock_TCP: Possible SYN flooding on port 443. Sending cookies.",
    ),
]

PROFILES: list[DemoProfile] = [
    DemoProfile(
        name="prod-web-01",
        hostname="web-01.prod.nightwatch.local",
        os="Ubuntu 24.04.1 LTS",
        cores=4,
        memory_total=8 * GIB,
        disk_total=80 * 10**9,
        cpu_base=14,
        cpu_daily=38,
        memory_base=52,
        disk_start=61,
        disk_growth_per_day=0.35,
        net_scale=9_000_000,
        services={
            "nginx": "active",
            "gunicorn": "active",
            "ssh": "active",
            "fail2ban": "active",
            "cron": "active",
        },
        log_templates=[
            *COMMON_LOGS,
            (
                "error",
                "nginx",
                "upstream timed out (110: Connection timed out) while reading "
                'response header from upstream, client: {ip}, request: "GET /api/orders HTTP/1.1"',
            ),
            (
                "error",
                "nginx",
                "connect() failed (111: Connection refused) while connecting to "
                'upstream, upstream: "http://127.0.0.1:8000/"',
            ),
            (
                "warning",
                "nginx",
                "an upstream response is buffered to a temporary file "
                "/var/cache/nginx/proxy_temp/{n}",
            ),
            ("info", "gunicorn", "Booting worker with pid: {pid}"),
            ("warning", "gunicorn", "Worker with pid {pid} was terminated due to signal 9"),
            ("error", "gunicorn", "[CRITICAL] WORKER TIMEOUT (pid:{pid})"),
            (
                "error",
                "gunicorn",
                "django.db.utils.OperationalError: connection to server at "
                '"10.0.1.20", port 5432 failed: timeout expired',
            ),
            ("info", "fail2ban", "[sshd] Ban {ip}"),
        ],
    ),
    DemoProfile(
        name="prod-db-01",
        hostname="db-01.prod.nightwatch.local",
        os="Debian GNU/Linux 12 (bookworm)",
        cores=8,
        memory_total=32 * GIB,
        disk_total=500 * 10**9,
        cpu_base=9,
        cpu_daily=22,
        memory_base=78,
        disk_start=69,
        disk_growth_per_day=0.6,
        net_scale=25_000_000,
        services={
            "postgresql": "active",
            "pgbouncer": "active",
            "ssh": "active",
            "prometheus-node-exporter": "active",
        },
        log_templates=[
            *COMMON_LOGS,
            (
                "info",
                "postgres",
                "checkpoint complete: wrote {n} buffers (2.1%); 0 WAL file(s) added",
            ),
            (
                "warning",
                "postgres",
                "checkpoints are occurring too frequently ({sec} seconds apart)",
            ),
            ("info", "postgres", 'automatic vacuum of table "shop.public.orders": index scans: 1'),
            (
                "warning",
                "postgres",
                "duration: {ms} ms  statement: SELECT * FROM orders WHERE "
                "customer_id = $1 ORDER BY created_at DESC",
            ),
            (
                "error",
                "postgres",
                "deadlock detected: Process {pid} waits for ShareLock on "
                "transaction {n}; blocked by process {pid2}",
            ),
            ("error", "pgbouncer", "closing because: client_idle_timeout (age={sec}s)"),
            (
                "critical",
                "postgres",
                'could not extend file "base/16384/2619": No space left on device',
            ),
        ],
    ),
    DemoProfile(
        name="staging-app",
        hostname="staging.nightwatch.local",
        os="Ubuntu 22.04.5 LTS",
        cores=2,
        memory_total=4 * GIB,
        disk_total=40 * 10**9,
        cpu_base=22,
        cpu_daily=30,
        memory_base=64,
        disk_start=84,
        disk_growth_per_day=0.7,
        net_scale=2_000_000,
        services={
            "docker": "active",
            "nginx": "active",
            "ssh": "active",
            "celery-worker": "failed",
        },
        log_templates=[
            *COMMON_LOGS,
            (
                "error",
                "celery",
                "consumer: Cannot connect to redis://localhost:6379/0: "
                "Error 111 connecting to localhost:6379. Connection refused.",
            ),
            (
                "critical",
                "systemd",
                "celery-worker.service: Main process exited, code=exited, status=1/FAILURE",
            ),
            ("error", "systemd", "celery-worker.service: Failed with result 'exit-code'."),
            ("warning", "dockerd", "Health check for container {hex} failed: timeout"),
            ("info", "dockerd", "Container {hex} started"),
            (
                "critical",
                "kernel",
                "Out of memory: Killed process {pid} (python3) "
                "total-vm:2148320kB, anon-rss:1630232kB",
            ),
            (
                "warning",
                "kernel",
                "EXT4-fs warning (device sda1): ext4_dx_add_entry: Directory index full!",
            ),
        ],
    ),
    DemoProfile(
        name="edge-proxy-fra",
        hostname="edge-fra-1.nightwatch.local",
        os="Rocky Linux 9.4 (Blue Onyx)",
        cores=2,
        memory_total=2 * GIB,
        disk_total=30 * 10**9,
        cpu_base=6,
        cpu_daily=14,
        memory_base=33,
        disk_start=24,
        disk_growth_per_day=0.05,
        net_scale=60_000_000,
        services={"haproxy": "active", "keepalived": "active", "sshd": "active"},
        log_templates=[
            *COMMON_LOGS,
            ("info", "haproxy", "Server backend_web/web-01 is UP, reason: Layer7 check passed"),
            (
                "warning",
                "haproxy",
                "Server backend_web/web-02 is DOWN, reason: Layer4 timeout, check duration: 2001ms",
            ),
            ("info", "keepalived", "(VI_1) Entering MASTER STATE"),
            ("warning", "haproxy", "backend_web has no server available!"),
        ],
    ),
    DemoProfile(
        name="backup-node",
        hostname="backup-01.nightwatch.local",
        os="Debian GNU/Linux 12 (bookworm)",
        cores=2,
        memory_total=4 * GIB,
        disk_total=2000 * 10**9,
        cpu_base=4,
        cpu_daily=8,
        memory_base=21,
        disk_start=72,
        disk_growth_per_day=0.4,
        net_scale=40_000_000,
        services={"borgbackup.timer": "active", "ssh": "active"},
        log_templates=[
            *COMMON_LOGS,
            ("info", "borg", "Archive fingerprint: {hex}; This archive: {n} MB"),
            ("error", "borg", "Remote: Connection closed by remote host"),
            ("critical", "kernel", "ata1.00: failed command: READ FPDMA QUEUED"),
        ],
        offline_for=timedelta(hours=3, minutes=12),
    ),
]
PROFILES_BY_NAME = {p.name: p for p in PROFILES}


# --- Signal generation -----------------------------------------------------------------


def _daily_wave(moment: datetime) -> float:
    """0..1, low at night, peak around 15:00 local time."""
    local = timezone.localtime(moment)
    hour = local.hour + local.minute / 60
    return (math.sin((hour - 9) / 24 * 2 * math.pi) + 1) / 2


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def metrics_at(profile: DemoProfile, moment: datetime, now: datetime, rng: random.Random) -> dict:
    wave = _daily_wave(moment)
    cpu = profile.cpu_base + profile.cpu_daily * wave + rng.gauss(0, 3.5)
    if rng.random() < 0.012:  # occasional spike: deploy, cron job, traffic burst
        cpu += rng.uniform(25, 45)
    cpu = _clamp(cpu, 0.5, 99.5)

    # Slow leak that resets on a restart every ~2 days: a classic sawtooth.
    leak = ((moment.timestamp() / 3600) % 52) / 52 * 9
    memory_percent = _clamp(profile.memory_base + 5 * wave + leak + rng.gauss(0, 0.8), 5, 97)

    days_ago = (now - moment).total_seconds() / 86_400
    disk_percent = _clamp(profile.disk_start + profile.disk_growth_per_day * (7 - days_ago), 1, 99)

    load_1 = max(0.0, cpu / 100 * profile.cores * rng.uniform(0.8, 1.25))
    traffic = profile.net_scale * (cpu / 100) * rng.uniform(0.7, 1.3)
    boot = now - timedelta(days=profile.boot_days_ago)

    return {
        "cpu_percent": round(cpu, 2),
        "memory_percent": round(memory_percent, 2),
        "memory_used": int(profile.memory_total * memory_percent / 100),
        "memory_total": profile.memory_total,
        "disk_percent": round(disk_percent, 2),
        "disk_used": int(profile.disk_total * disk_percent / 100),
        "disk_total": profile.disk_total,
        "net_sent_bps": round(traffic * 0.35, 2),
        "net_recv_bps": round(traffic, 2),
        "load_1": round(load_1, 2),
        "load_5": round(load_1 * rng.uniform(0.85, 1.05), 2),
        "load_15": round(load_1 * rng.uniform(0.75, 0.95), 2),
        "uptime_seconds": max(0, int((moment - boot).total_seconds())),
    }


def fake_log(profile: DemoProfile, moment: datetime, rng: random.Random) -> dict:
    weights = {"debug": 1, "info": 10, "warning": 5, "error": 3, "critical": 0.6}
    level, source, template = rng.choices(
        profile.log_templates, weights=[weights[t[0]] for t in profile.log_templates]
    )[0]
    message = template.format(
        ip=f"{rng.choice([45, 91, 185, 203])}.{rng.randint(1, 254)}.{rng.randint(1, 254)}"
        f".{rng.randint(1, 254)}",
        port=rng.randint(30_000, 65_000),
        user=rng.choice(["admin", "test", "oracle", "ubuntu", "git", "postgres"]),
        n=rng.randint(100, 99_999),
        pid=rng.randint(1_000, 60_000),
        pid2=rng.randint(1_000, 60_000),
        sec=rng.randint(3, 29),
        ms=rng.randint(1_200, 9_800),
        hex=f"{rng.getrandbits(48):012x}",
    )
    return {"timestamp": moment, "level": level, "source": source, "message": message}


def _history_points(start: datetime, end: datetime) -> Iterator[datetime]:
    """Dense recent history, sparse old history: enough for every chart range, few rows."""
    moment = start
    while moment <= end:
        yield moment
        age = end - moment
        if age > timedelta(hours=24):
            moment += timedelta(minutes=10)
        elif age > timedelta(hours=1):
            moment += timedelta(minutes=2)
        else:
            moment += timedelta(seconds=15)


# --- Public API ------------------------------------------------------------------------------


@transaction.atomic
def create_demo_server(profile: DemoProfile, now: datetime, seed: int = 42) -> Server:
    rng = random.Random(f"{seed}-{profile.name}")
    last_report = now - (profile.offline_for or timedelta(0))
    server, _ = Server.create_with_token(
        name=profile.name,
        hostname=profile.hostname,
        os=profile.os,
        agent_version="0.1.0",
        is_demo=True,
        last_seen=last_report,
    )

    MetricSnapshot.objects.bulk_create(
        (
            MetricSnapshot(server=server, timestamp=moment, **metrics_at(profile, moment, now, rng))
            for moment in _history_points(now - timedelta(days=7), last_report)
        ),
        batch_size=1_000,
    )

    for name, state in profile.services.items():
        server.services.create(name=name, status=state, updated_at=last_report)

    log_times = sorted(
        last_report - timedelta(seconds=rng.expovariate(1 / 21_600)) for _ in range(220)
    )
    LogEntry.objects.bulk_create(
        LogEntry(server=server, **fake_log(profile, moment, rng)) for moment in log_times
    )
    return server


def seed(now: datetime | None = None) -> list[Server]:
    now = now or timezone.now()
    return [create_demo_server(profile, now) for profile in PROFILES]


def demo_queryset():
    return Server.objects.filter(is_demo=True)


def tick(now: datetime | None = None, rng: random.Random | None = None) -> int:
    """Send one fresh report for every live demo server, through the real ingest code."""
    now = now or timezone.now()
    rng = rng or random.Random()
    sent = 0
    for server in demo_queryset():
        profile = PROFILES_BY_NAME.get(server.name)
        if profile is None or profile.offline_for is not None:
            continue
        logs = [fake_log(profile, now, rng) for _ in range(rng.choices([0, 1, 2], [70, 22, 8])[0])]
        ingest_report(
            server,
            {
                "hostname": profile.hostname,
                "os": profile.os,
                "agent_version": "0.1.0",
                "timestamp": now,
                "metrics": metrics_at(profile, now, now, rng),
                "services": [{"name": n, "status": s} for n, s in profile.services.items()],
                "logs": logs,
            },
        )
        sent += 1
    return sent
