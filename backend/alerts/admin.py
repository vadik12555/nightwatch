from django.contrib import admin

from .models import Alert, AlertRule


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "metric", "condition", "threshold", "duration", "severity", "server"]
    list_filter = ["is_enabled", "severity", "metric"]


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ["server", "kind", "severity", "status", "message", "opened_at", "resolved_at"]
    list_filter = ["status", "kind", "severity", "server"]
    search_fields = ["message"]
    list_select_related = ["server"]
    date_hierarchy = "opened_at"
