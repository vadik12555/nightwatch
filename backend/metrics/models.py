from django.db import models

from servers.models import Server


class MetricSnapshot(models.Model):
    """One point-in-time reading of system metrics sent by an agent."""

    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name="snapshots")
    timestamp = models.DateTimeField("время замера")

    cpu_percent = models.FloatField("CPU, %")
    memory_percent = models.FloatField("RAM, %")
    memory_used = models.BigIntegerField("RAM занято, байт")
    memory_total = models.BigIntegerField("RAM всего, байт")
    disk_percent = models.FloatField("диск, %")
    disk_used = models.BigIntegerField("диск занято, байт")
    disk_total = models.BigIntegerField("диск всего, байт")
    # Rates are computed by the agent from counter deltas between two reports.
    net_sent_bps = models.FloatField("исходящий трафик, байт/с", default=0)
    net_recv_bps = models.FloatField("входящий трафик, байт/с", default=0)
    load_1 = models.FloatField("load average 1m", default=0)
    load_5 = models.FloatField("load average 5m", default=0)
    load_15 = models.FloatField("load average 15m", default=0)
    uptime_seconds = models.BigIntegerField("аптайм, с", default=0)

    class Meta:
        ordering = ["-timestamp"]
        get_latest_by = "timestamp"
        verbose_name = "снимок метрик"
        verbose_name_plural = "снимки метрик"
        indexes = [
            # Every chart and "latest value" query filters by server and time range.
            models.Index(fields=["server", "-timestamp"], name="snapshot_server_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.server} @ {self.timestamp:%Y-%m-%d %H:%M:%S}"


class ServiceStatus(models.Model):
    """Current state of a systemd unit on a server (one row per server+service)."""

    class State(models.TextChoices):
        ACTIVE = "active", "active"
        INACTIVE = "inactive", "inactive"
        FAILED = "failed", "failed"
        ACTIVATING = "activating", "activating"
        DEACTIVATING = "deactivating", "deactivating"
        UNKNOWN = "unknown", "unknown"

    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name="services")
    name = models.CharField("сервис", max_length=255)
    status = models.CharField("состояние", max_length=16, choices=State.choices)
    updated_at = models.DateTimeField("обновлено")

    class Meta:
        ordering = ["name"]
        verbose_name = "статус сервиса"
        verbose_name_plural = "статусы сервисов"
        constraints = [
            models.UniqueConstraint(fields=["server", "name"], name="unique_service_per_server"),
        ]

    def __str__(self) -> str:
        return f"{self.server}: {self.name} ({self.status})"


class LogEntry(models.Model):
    """A log line collected by the agent from journald or a log file."""

    class Level(models.TextChoices):
        DEBUG = "debug", "DEBUG"
        INFO = "info", "INFO"
        WARNING = "warning", "WARNING"
        ERROR = "error", "ERROR"
        CRITICAL = "critical", "CRITICAL"

    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name="logs")
    timestamp = models.DateTimeField("время")
    level = models.CharField("уровень", max_length=10, choices=Level.choices)
    source = models.CharField("источник", max_length=255)
    message = models.TextField("сообщение")
    received_at = models.DateTimeField("получено", auto_now_add=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "запись лога"
        verbose_name_plural = "записи логов"
        indexes = [
            models.Index(fields=["server", "-timestamp"], name="log_server_ts_idx"),
            models.Index(fields=["server", "level", "-timestamp"], name="log_server_level_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.source}: {self.message[:60]}"
