"""Small presentation helpers for the templates (formatting only, no business logic)."""

from datetime import datetime

from django import template
from django.utils import timezone

register = template.Library()

WARN_PERCENT = 75
CRIT_PERCENT = 90


@register.filter
def meter_level(value: float | None) -> str:
    """CSS modifier for a usage bar: ok / warn / crit."""
    if value is None:
        return "none"
    if value >= CRIT_PERCENT:
        return "crit"
    if value >= WARN_PERCENT:
        return "warn"
    return "ok"


@register.filter
def css_percent(value: float | None) -> str:
    """Width for inline CSS. A string with a dot: localized floats ("12,5") would break CSS."""
    if value is None:
        return "0"
    return f"{max(0.0, min(100.0, float(value))):.1f}"


@register.filter
def duration(seconds: int | None) -> str:
    """Uptime: '12 д 3 ч', '5 ч 12 мин', '3 мин'."""
    if seconds is None:
        return "—"
    seconds = int(seconds)
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes = rest // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


@register.filter
def bytes_rate(value: float | None) -> str:
    """Network throughput in bytes per second, human readable."""
    if value is None:
        return "—"
    value = float(value)
    for unit in ("Б/с", "КБ/с", "МБ/с"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "Б/с" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ/с"


@register.filter
def ago(moment: datetime | None) -> str:
    """Compact relative time for live dashboards: '12 с назад', '3 мин назад'."""
    if moment is None:
        return "никогда"
    seconds = int((timezone.now() - moment).total_seconds())
    if seconds < 5:
        return "только что"
    if seconds < 60:
        return f"{seconds} с назад"
    if seconds < 3_600:
        return f"{seconds // 60} мин назад"
    if seconds < 86_400:
        return f"{seconds // 3_600} ч назад"
    return f"{seconds // 86_400} д назад"


@register.filter
def duration_short(seconds: float | None) -> str:
    """Alert durations: '45 с', '12 мин', '3 ч 10 мин', '2 д 4 ч'."""
    if seconds is None:
        return "—"
    from notifications.formatting import humanize_duration

    return humanize_duration(seconds)
