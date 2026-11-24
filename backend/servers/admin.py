from django.contrib import admin

from .models import Server


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ["name", "hostname", "os", "status_label", "last_seen", "is_demo"]
    list_filter = ["is_demo", "os"]
    search_fields = ["name", "hostname"]
    readonly_fields = [
        "hostname",
        "os",
        "agent_version",
        "token_prefix",
        "last_seen",
        "created_by",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="статус")
    def status_label(self, obj: Server) -> str:
        return obj.get_status_display()
