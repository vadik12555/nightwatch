from django.contrib import admin

from .models import TelegramChat


@admin.register(TelegramChat)
class TelegramChatAdmin(admin.ModelAdmin):
    list_display = ["user", "chat_id", "username", "title", "notify", "linked_at"]
    list_filter = ["notify"]
    search_fields = ["username", "title", "user__username"]
