from django.contrib import admin

from .models import ChatMessage, LogExplanation, Runbook, WeeklyReport


@admin.register(Runbook)
class RunbookAdmin(admin.ModelAdmin):
    list_display = ["title", "status", "source_name", "indexed_at"]
    list_filter = ["status"]
    search_fields = ["title", "content"]


@admin.register(LogExplanation)
class LogExplanationAdmin(admin.ModelAdmin):
    list_display = ["source", "short_message", "status", "hits", "model", "created_at"]
    list_filter = ["status"]
    search_fields = ["sample_message", "source"]

    @admin.display(description="сообщение")
    def short_message(self, obj: LogExplanation) -> str:
        return obj.sample_message[:100]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "server", "status", "created_at"]
    list_filter = ["role", "status"]


@admin.register(WeeklyReport)
class WeeklyReportAdmin(admin.ModelAdmin):
    list_display = ["period_start", "period_end", "ai_generated", "sent_to", "created_at"]
