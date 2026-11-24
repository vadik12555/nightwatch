from django.urls import path

from . import views

app_name = "servers"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("servers/new/", views.server_create, name="create"),
    path("servers/<int:pk>/", views.server_detail, name="detail"),
    path("servers/<int:pk>/install/", views.server_install, name="install"),
    path("servers/<int:pk>/install/status/", views.server_install_status, name="install_status"),
    path("servers/<int:pk>/token/rotate/", views.server_rotate_token, name="rotate_token"),
    path("servers/<int:pk>/delete/", views.server_delete, name="delete"),
    # HTMX partials and chart data
    path("servers/<int:pk>/status/", views.server_status, name="status"),
    path("servers/<int:pk>/stats/", views.server_stats, name="stats"),
    path("servers/<int:pk>/services/", views.server_services, name="services"),
    path("servers/<int:pk>/logs/", views.server_logs, name="logs"),
    path("servers/<int:pk>/metrics.json", views.server_metrics, name="metrics"),
    # Agent distribution (public: fetched by curl on the monitored server)
    path("agent/<str:filename>", views.agent_file, name="agent_file"),
]
