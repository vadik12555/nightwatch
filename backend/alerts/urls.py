from django.urls import path

from . import views

app_name = "alerts"

urlpatterns = [
    path("", views.alert_list, name="list"),
    path("rules/", views.rule_list, name="rules"),
    path("rules/new/", views.rule_edit, name="rule_create"),
    path("rules/<int:pk>/", views.rule_edit, name="rule_edit"),
    path("rules/<int:pk>/toggle/", views.rule_toggle, name="rule_toggle"),
    path("rules/<int:pk>/delete/", views.rule_delete, name="rule_delete"),
]
