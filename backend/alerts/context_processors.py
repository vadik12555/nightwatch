from .models import Alert


def open_alerts(request) -> dict:
    """Number of open alerts for the red badge in the top navigation."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"nav_open_alerts": Alert.objects.open().count()}
