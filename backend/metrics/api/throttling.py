from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle

from servers.models import Server


class ServerRateThrottle(SimpleRateThrottle):
    """Rate limit per server token (not per IP: many servers can sit behind one NAT).

    Counters live in the Django cache — Redis in Docker, so the limit is shared
    by all Gunicorn workers. The rate comes from settings at request time,
    which keeps it configurable via env and overridable in tests.
    """

    scope = "ingest"

    def get_rate(self) -> str:
        return settings.NIGHTWATCH_INGEST_RATE

    def get_cache_key(self, request, view) -> str:
        if isinstance(request.auth, Server):
            ident = f"server-{request.auth.pk}"
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
