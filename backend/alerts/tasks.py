import logging

from celery import shared_task
from django.core.cache import cache

from . import engine

logger = logging.getLogger(__name__)

LOCK_KEY = "nightwatch:alerts:evaluate-lock"
LOCK_TTL = 120  # seconds; the lock expires even if a worker dies mid-run


@shared_task
def evaluate_alerts() -> dict[str, int] | None:
    """Runs every 30 s (Celery beat). A cache lock prevents overlapping runs:
    `cache.add` is atomic in Redis, so only one worker gets it."""
    if not cache.add(LOCK_KEY, "1", timeout=LOCK_TTL):
        logger.info("Previous alert evaluation is still running — skipping this tick")
        return None
    try:
        result = engine.evaluate()
    finally:
        cache.delete(LOCK_KEY)
    return {"opened": len(result.opened), "resolved": len(result.resolved)}
