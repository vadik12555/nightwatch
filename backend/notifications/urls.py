from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.telegram_settings, name="telegram"),
    path("code/", views.issue_link_code, name="issue_code"),
    path("chats/<int:pk>/toggle/", views.toggle_chat, name="toggle_chat"),
    path("chats/<int:pk>/unlink/", views.unlink_chat, name="unlink_chat"),
]
