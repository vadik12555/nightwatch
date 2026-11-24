from django.contrib import admin

from .models import LogEntry, MetricSnapshot, ServiceStatus


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        "server",
        "timestamp",
        "cpu_percent",
        "memory_percent",
        "disk_percent",
        "load_1",
    ]
    list_filter = ["server"]
    date_hierarchy = "timestamp"
    list_select_related = ["server"]


@admin.register(ServiceStatus)
class ServiceStatusAdmin(admin.ModelAdmin):
    list_display = ["server", "name", "status", "updated_at"]
    list_filter = ["status", "server"]
    search_fields = ["name"]
    list_select_related = ["server"]


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ["server", "timestamp", "level", "source", "short_message"]
    list_filter = ["level", "server"]
    search_fields = ["message", "source"]
    date_hierarchy = "timestamp"
    list_select_related = ["server"]

    @admin.display(description="сообщение")
    def short_message(self, obj: LogEntry) -> str:
        return obj.message[:120]
