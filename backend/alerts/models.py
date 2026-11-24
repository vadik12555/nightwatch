import operator
from datetime import timedelta

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from servers.models import Server


class Severity(models.TextChoices):
    WARNING = "warning", "Warning"
    CRITICAL = "critical", "Critical"


class AlertRule(models.Model):
    """«Метрика <условие> порог дольше N»: e.g. CPU > 90 % for 5 minutes.

    The duration filters out short spikes: the rule fires only if *every*
    reading in the last N seconds matches the condition.
    """

    class Metric(models.TextChoices):
        CPU = "cpu_percent", "CPU, %"
        MEMORY = "memory_percent", "RAM, %"
        DISK = "disk_percent", "Диск, %"
        LOAD_1 = "load_1", "Load average 1m"
        LOAD_5 = "load_5", "Load average 5m"
        LOAD_15 = "load_15", "Load average 15m"

    class Condition(models.TextChoices):
        GT = "gt", ">"
        GTE = "gte", "≥"
        LT = "lt", "<"
        LTE = "lte", "≤"

    DURATION_CHOICES = [
        (0, "сразу"),
        (60, "1 мин"),
        (120, "2 мин"),
        (300, "5 мин"),
        (600, "10 мин"),
        (900, "15 мин"),
        (1800, "30 мин"),
        (3600, "1 час"),
    ]
    PERCENT_METRICS = {Metric.CPU, Metric.MEMORY, Metric.DISK}
    OPERATORS = {
        Condition.GT: operator.gt,
        Condition.GTE: operator.ge,
        Condition.LT: operator.lt,
        Condition.LTE: operator.le,
    }

    name = models.CharField("название", max_length=100)
    metric = models.CharField("метрика", max_length=20, choices=Metric.choices)
    condition = models.CharField("условие", max_length=3, choices=Condition.choices)
    threshold = models.FloatField("порог", validators=[MinValueValidator(0)])
    duration = models.PositiveIntegerField(
        "длительность, с",
        choices=DURATION_CHOICES,
        default=300,
        help_text="Сколько секунд условие должно выполняться без перерыва.",
    )
    severity = models.CharField(
        "важность", max_length=10, choices=Severity.choices, default=Severity.WARNING
    )
    server = models.ForeignKey(
        Server,
        verbose_name="сервер",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="alert_rules",
        help_text="Пусто — правило действует на все серверы.",
    )
    is_enabled = models.BooleanField("включено", default=True)
    created_at = models.DateTimeField("создано", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "правило алерта"
        verbose_name_plural = "правила алертов"

    def __str__(self) -> str:
        return self.name

    @property
    def is_percent(self) -> bool:
        return self.metric in self.PERCENT_METRICS

    def matches(self, value: float) -> bool:
        return self.OPERATORS[self.condition](value, self.threshold)

    def format_value(self, value: float) -> str:
        return f"{value:.1f}%" if self.is_percent else f"{value:.2f}"

    @property
    def expression(self) -> str:
        """Human-readable condition, e.g. «CPU, % > 90% дольше 5 мин»."""
        metric = self.Metric(self.metric).label.split(",")[0]
        text = f"{metric} {self.get_condition_display()} {self.format_value(self.threshold)}"
        if self.duration:
            text += f" дольше {self.get_duration_display()}"
        return text


class AlertQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status=Alert.Status.OPEN)


class Alert(models.Model):
    """A problem on a server: opened when a check starts failing, closed when it recovers."""

    class Kind(models.TextChoices):
        METRIC = "metric", "Метрика"
        OFFLINE = "offline", "Сервер недоступен"
        SERVICE = "service", "Сервис упал"

    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        RESOLVED = "resolved", "Закрыт"

    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name="alerts")
    rule = models.ForeignKey(
        AlertRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts"
    )
    kind = models.CharField("тип", max_length=10, choices=Kind.choices)
    # What exactly is wrong, e.g. "rule:3", "offline", "service:nginx". At most one
    # open alert per (server, key) — enforced by the partial unique index below.
    key = models.CharField(max_length=300)
    severity = models.CharField("важность", max_length=10, choices=Severity.choices)
    status = models.CharField(
        "статус", max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    message = models.CharField("сообщение", max_length=500)
    value = models.FloatField("значение", null=True, blank=True)
    opened_at = models.DateTimeField("открыт", default=timezone.now)
    resolved_at = models.DateTimeField("закрыт", null=True, blank=True)

    objects = AlertQuerySet.as_manager()

    class Meta:
        ordering = ["-opened_at"]
        verbose_name = "алерт"
        verbose_name_plural = "алерты"
        constraints = [
            models.UniqueConstraint(
                fields=["server", "key"],
                condition=Q(status="open"),
                name="one_open_alert_per_server_key",
            ),
        ]
        indexes = [models.Index(fields=["status", "-opened_at"], name="alert_status_opened_idx")]

    def __str__(self) -> str:
        return f"[{self.status}] {self.server}: {self.message}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def duration(self) -> timedelta:
        return (self.resolved_at or timezone.now()) - self.opened_at
