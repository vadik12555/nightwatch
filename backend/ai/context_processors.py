from django.conf import settings


def ai_status(request) -> dict:
    return {"ai_enabled": bool(settings.LLM_API_KEY)}
