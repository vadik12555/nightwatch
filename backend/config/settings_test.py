"""Settings for the pytest run: fast, isolated, no external services required."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
os.environ.pop("REDIS_URL", None)  # tests never touch a real Redis

from .settings import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"
NIGHTWATCH_DEMO_MODE = False
NIGHTWATCH_PUBLIC_URL = ""
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_BOT_USERNAME = "nightwatch_test_bot"
LLM_API_KEY = ""
LLM_BASE_URL = "http://llm.test/v1"
LLM_MODEL = "test-model"
LLM_EMBEDDING_MODEL = "test-embed"
LOGGING = {"version": 1, "disable_existing_loggers": False}
