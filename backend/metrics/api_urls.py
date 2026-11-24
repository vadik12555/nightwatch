from django.contrib.auth.decorators import login_not_required
from django.urls import path

from .api.views import IngestView

app_name = "api"

urlpatterns = [
    # Agents authenticate with their own token, so the session-login middleware is skipped.
    path("ingest/", login_not_required(IngestView.as_view()), name="ingest"),
]
