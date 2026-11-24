from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    path("", views.chat, name="chat"),
    path("chat/send/", views.chat_send, name="chat_send"),
    path("chat/messages/<int:pk>/", views.chat_message, name="chat_message"),
    path("chat/clear/", views.chat_clear, name="chat_clear"),
    path("logs/<int:log_id>/explain/", views.explain_log, name="explain"),
    path("explanations/<int:pk>/", views.explanation_status, name="explanation"),
    path("runbooks/", views.runbooks, name="runbooks"),
    path("runbooks/samples/", views.load_samples, name="load_samples"),
    path("runbooks/<int:pk>/", views.runbook_detail, name="runbook"),
    path("runbooks/<int:pk>/reindex/", views.runbook_reindex, name="runbook_reindex"),
    path("runbooks/<int:pk>/delete/", views.runbook_delete, name="runbook_delete"),
    path("reports/", views.reports, name="reports"),
    path("reports/run/", views.reports_run, name="reports_run"),
]
