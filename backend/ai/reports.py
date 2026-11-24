"""Weekly AI summary of infrastructure health, delivered to Telegram.

Facts are computed with plain SQL aggregates (reliable numbers); the LLM only turns
them into readable text. If the LLM is unavailable, the facts themselves are sent —
the report must never be lost because of the AI part.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta
from html import escape

from django.db.models import Avg, Max
from django.utils import timezone

from alerts.models import Alert
from metrics.models import LogEntry, MetricSnapshot
from notifications import telegram
from notifications.models import TelegramChat
from servers.models import Server

from . import prompts
from .fingerprint import normalize
from .models import WeeklyReport
from .providers import LLMError, LLMProvider, get_provider

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 3900  # Telegram allows 4096 characters per message


def collect_facts(end: datetime | None = None, days: int = 7) -> dict:
    end = end or timezone.now()
    start = end - timedelta(days=days)

    metrics = {
        row["server"]: row
        for row in MetricSnapshot.objects.filter(timestamp__gte=start, timestamp__lt=end)
        .values("server")
        .annotate(
            cpu_avg=Avg("cpu_percent"),
            cpu_max=Max("cpu_percent"),
            mem_avg=Avg("memory_percent"),
            mem_max=Max("memory_percent"),
            disk_max=Max("disk_percent"),
            load_max=Max("load_1"),
        )
    }
    alerts = Alert.objects.filter(opened_at__gte=start, opened_at__lt=end)
    alert_counts = Counter(alerts.values_list("server_id", flat=True))
    kind_counts = dict(Counter(alerts.values_list("kind", flat=True)))
    offline_minutes: Counter = Counter()
    for alert in alerts.filter(kind=Alert.Kind.OFFLINE):
        offline_minutes[alert.server_id] += int(alert.duration.total_seconds() // 60)

    servers = []
    for server in Server.objects.order_by("name"):
        row = metrics.get(server.pk)
        servers.append(
            {
                "name": server.name,
                "status": server.status,
                "cpu_avg": round(row["cpu_avg"], 1) if row else None,
                "cpu_max": round(row["cpu_max"], 1) if row else None,
                "mem_avg": round(row["mem_avg"], 1) if row else None,
                "mem_max": round(row["mem_max"], 1) if row else None,
                "disk_max": round(row["disk_max"], 1) if row else None,
                "load_max": round(row["load_max"], 2) if row else None,
                "alerts": alert_counts.get(server.pk, 0),
                "offline_minutes": offline_minutes.get(server.pk, 0),
            }
        )

    # Most frequent errors, grouped like the "Explain" cache (numbers/IPs/PIDs ignored).
    error_groups: Counter = Counter()
    samples: dict[tuple[str, str], str] = {}
    errors = LogEntry.objects.filter(
        timestamp__gte=start, timestamp__lt=end, level__in=["error", "critical"]
    ).values_list("source", "message")[:20_000]
    for source, message in errors:
        key = (source, normalize(message))
        error_groups[key] += 1
        samples.setdefault(key, message[:200])

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "servers": servers,
        "alerts_total": alerts.count(),
        "alerts_by_kind": kind_counts,
        "open_alerts_now": Alert.objects.open().count(),
        "top_errors": [
            {"source": source, "count": count, "sample": samples[(source, pattern)]}
            for (source, pattern), count in error_groups.most_common(5)
        ],
        "log_errors_total": sum(error_groups.values()),
    }


def facts_to_text(facts: dict) -> str:
    start = timezone.localtime(datetime.fromisoformat(facts["period_start"]))
    end = timezone.localtime(datetime.fromisoformat(facts["period_end"]))
    lines = [f"Период: {start:%d.%m.%Y} – {end:%d.%m.%Y}", "", "Серверы:"]
    for s in facts["servers"]:
        if s["cpu_avg"] is None:
            lines.append(f"- {s['name']}: нет данных за период (статус {s['status']})")
            continue
        line = (
            f"- {s['name']} ({s['status']}): CPU ср. {s['cpu_avg']}% / макс {s['cpu_max']}%, "
            f"RAM ср. {s['mem_avg']}% / макс {s['mem_max']}%, диск макс {s['disk_max']}%, "
            f"load макс {s['load_max']}, алертов {s['alerts']}"
        )
        if s["offline_minutes"]:
            line += f", недоступен {s['offline_minutes']} мин"
        lines.append(line)
    kinds = ", ".join(f"{k}: {v}" for k, v in facts["alerts_by_kind"].items()) or "нет"
    lines += [
        "",
        f"Алертов за неделю: {facts['alerts_total']} ({kinds}); открыто сейчас: "
        f"{facts['open_alerts_now']}",
        f"Ошибок в логах (error/critical): {facts['log_errors_total']}",
    ]
    if facts["top_errors"]:
        lines.append("Самые частые ошибки:")
        lines += [f"- {e['source']} ×{e['count']}: {e['sample']}" for e in facts["top_errors"]]
    return "\n".join(lines)


def build_weekly_report(
    end: datetime | None = None, provider: LLMProvider | None = None
) -> WeeklyReport:
    facts = collect_facts(end)
    text = facts_to_text(facts)
    try:
        provider = provider or get_provider()
        summary, ai_generated = provider.chat(prompts.weekly_messages(text), temperature=0.3), True
    except LLMError as exc:
        logger.warning("Weekly report without AI: %s", exc)
        summary, ai_generated = text, False
    return WeeklyReport.objects.create(
        period_start=datetime.fromisoformat(facts["period_start"]),
        period_end=datetime.fromisoformat(facts["period_end"]),
        facts=facts,
        summary=summary,
        ai_generated=ai_generated,
    )


def telegram_text(report: WeeklyReport) -> str:
    start = timezone.localtime(report.period_start)
    end = timezone.localtime(report.period_end)
    header = f"📊 <b>NightWatch: сводка за {start:%d.%m}–{end:%d.%m}</b>"
    if not report.ai_generated:
        header += "\n<i>(AI недоступен — отправлены только цифры)</i>"
    body = escape(report.summary)
    if len(body) > TELEGRAM_LIMIT:
        body = body[:TELEGRAM_LIMIT] + "…"
    return f"{header}\n\n{body}"


def send_report(report: WeeklyReport) -> int:
    if not telegram.is_configured():
        return 0
    chat_ids = list(TelegramChat.objects.filter(notify=True).values_list("chat_id", flat=True))
    results = telegram.send_messages(chat_ids, telegram_text(report))
    sent = sum(1 for error in results.values() if error is None)
    report.sent_to = sent
    report.save(update_fields=["sent_to"])
    return sent
