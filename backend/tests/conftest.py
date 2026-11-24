from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from servers.models import Server


@pytest.fixture(autouse=True)
def _clear_cache():
    # Throttle counters live in the cache; keep tests independent of each other.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="alice", password="s3cret-pass")


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def server_and_token(db):
    return Server.create_with_token(name="web-01")


@pytest.fixture
def server(server_and_token):
    return server_and_token[0]


@pytest.fixture
def token(server_and_token):
    return server_and_token[1]


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def agent_client(api_client, token):
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    return api_client


@pytest.fixture
def make_report():
    """Factory for a valid agent report; override any top-level key via kwargs."""

    def factory(**overrides):
        now = timezone.now()
        report = {
            "hostname": "web-01.example.com",
            "os": "Ubuntu 24.04 LTS",
            "agent_version": "0.1.0",
            "timestamp": now.isoformat(),
            "metrics": {
                "cpu_percent": 12.5,
                "memory_percent": 41.0,
                "memory_used": 3_300_000_000,
                "memory_total": 8_000_000_000,
                "disk_percent": 55.0,
                "disk_used": 44_000_000_000,
                "disk_total": 80_000_000_000,
                "net_sent_bps": 15_000.0,
                "net_recv_bps": 120_000.0,
                "load_1": 0.42,
                "load_5": 0.38,
                "load_15": 0.30,
                "uptime_seconds": 86_400,
            },
            "services": [
                {"name": "nginx", "status": "active"},
                {"name": "postgresql", "status": "failed"},
            ],
            "logs": [
                {
                    "timestamp": (now - timedelta(seconds=5)).isoformat(),
                    "level": "error",
                    "source": "nginx",
                    "message": "upstream timed out (110: Connection timed out)",
                },
                {
                    "timestamp": now.isoformat(),
                    "level": "info",
                    "source": "sshd",
                    "message": "Accepted publickey for deploy from 10.0.0.5",
                },
            ],
        }
        report.update(overrides)
        return report

    return factory
