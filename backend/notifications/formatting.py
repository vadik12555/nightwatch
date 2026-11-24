"""Texts the bot sends. Telegram HTML parse mode: every dynamic value goes through escape()."""

from html import escape

from django.conf import settings
from django.utils import timezone

from alerts.models import Alert, Severity
from servers.models import Server

SEVERITY_ICONS = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠"}
STATUS_ICONS = {
    Server.Status.ONLINE: "🟢",
    Server.Status.OFFLINE: "🔴",
    Server.Status.PENDING: "⚪",
}

NOT_LINKED = (
    "Этот чат не привязан к аккаунту NightWatch.\n\n"
    "Откройте в веб-интерфейсе <b>Telegram → Привязать чат</b> и отправьте боту "
    "команду <code>/start КОД</code>."
)
HELP = (
    "<b>NightWatch</b> — мониторинг серверов\n\n"
    "/status — сводка: серверы и активные алерты\n"
    "/servers — серверы с CPU / RAM / диском\n"
    "/unlink — отвязать этот чат\n"
    "/help — эта справка\n\n"
    "Уведомления об алертах и восстановлении приходят сюда автоматически."
)


def humanize_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} с"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч"


def local_time(moment) -> str:
    return timezone.localtime(moment).strftime("%d.%m %H:%M")


def server_url(server: Server) -> str | None:
    """Absolute link to the server page; only when the public URL is configured."""
    base = settings.NIGHTWATCH_PUBLIC_URL
    return f"{base}{server.get_absolute_url()}" if base else None


def alert_message(alert: Alert, event: str) -> str:
    server = escape(alert.server.name)
    message = escape(alert.message)
    if event == "resolved":
        return (
            f"✅ <b>Восстановлено</b> · <b>{server}</b>\n"
            f"{message}\n"
            f"⏱ длилось {humanize_duration(alert.duration.total_seconds())}"
        )
    icon = SEVERITY_ICONS.get(alert.severity, "🔴")
    return (
        f"{icon} <b>{alert.get_severity_display().upper()}</b> · <b>{server}</b>\n"
        f"{message}\n"
        f"🕒 {local_time(alert.opened_at)}"
    )


def status_text(servers: list[Server], summary: dict, open_alerts: list[Alert]) -> str:
    lines = [
        "<b>NightWatch · сводка</b>",
        f"Серверы: {summary['total']} · 🟢 {summary['online']} · 🔴 {summary['offline']}"
        + (f" · ⚪ {summary['pending']}" if summary["pending"] else ""),
        "",
    ]
    if not open_alerts:
        lines.append("✅ Активных алертов нет")
    else:
        lines.append(f"🚨 Активные алерты: {len(open_alerts)}")
        for alert in open_alerts[:10]:
            icon = SEVERITY_ICONS.get(alert.severity, "🔴")
            since = humanize_duration(alert.duration.total_seconds())
            lines.append(
                f"{icon} <b>{escape(alert.server.name)}</b>: {escape(alert.message)} ({since})"
            )
        if len(open_alerts) > 10:
            lines.append(f"…и ещё {len(open_alerts) - 10}")
    return "\n".join(lines)


def servers_text(servers: list[Server]) -> str:
    if not servers:
        return "Серверов пока нет. Добавьте первый в веб-интерфейсе."
    lines = ["<b>Серверы</b>"]
    for server in servers:
        icon = STATUS_ICONS[server.status]
        line = f"{icon} <b>{escape(server.name)}</b>"
        snapshot = server.latest
        if snapshot and server.status == Server.Status.ONLINE:
            line += (
                f"\n    CPU {snapshot.cpu_percent:.0f}% · RAM {snapshot.memory_percent:.0f}%"
                f" · диск {snapshot.disk_percent:.0f}% · LA {snapshot.load_1:.2f}"
            )
        elif server.status == Server.Status.OFFLINE:
            silent = (timezone.now() - server.last_seen).total_seconds()
            line += f"\n    нет связи {humanize_duration(silent)}"
        else:
            line += "\n    ждёт первый отчёт агента"
        if server.open_alerts:
            line += f"\n    🚨 алертов: {server.open_alerts}"
        lines.append(line)
    return "\n".join(lines)
