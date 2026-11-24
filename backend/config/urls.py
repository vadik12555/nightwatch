from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("api/v1/", include("metrics.api_urls")),
    path("healthz/", views.healthz, name="healthz"),
    path("", include("servers.urls")),
]
