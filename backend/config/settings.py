"""Django settings for NightWatch.

All environment-specific values come from environment variables (12-factor app).
Locally they can be put into a `.env` file in the repository root; in Docker
Compose the same file is passed to the containers via `env_file`.
"""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # .../backend
REPO_DIR = BASE_DIR.parent

# Values already present in the environment always win over the .env file.
load_dotenv(REPO_DIR / ".env", override=False)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# --- Core -------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", default=False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
    SECRET_KEY = "insecure-dev-only-key"  # noqa: S105 - used only with DEBUG=1

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    # Local apps
    "accounts",
    "servers",
    "metrics",
    "alerts",
    "notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Secure by default: every view requires login unless marked @login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "alerts.context_processors.open_alerts",
            ],
        },
    },
]

# --- Database & cache ---------------------------------------------------------

# PostgreSQL in Docker/production; SQLite is a zero-setup fallback for quick local hacking.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = os.environ.get("REDIS_URL", "")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "nightwatch",
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Auth ---------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "servers:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- I18N ---------------------------------------------------------------------

LANGUAGE_CODE = "ru"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Europe/Moscow")
USE_I18N = True
USE_TZ = True

# --- Static files -------------------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"  # collected here, served by Nginx in Docker

# --- Security -------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", default=False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# Nginx terminates TLS and tells Django the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", default=False)

# --- Django REST Framework -----------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": (
        ["rest_framework.renderers.JSONRenderer", "rest_framework.renderers.BrowsableAPIRenderer"]
        if DEBUG
        else ["rest_framework.renderers.JSONRenderer"]
    ),
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

# --- Celery -----------------------------------------------------------------------

# Without Redis (e.g. quick local run) tasks execute synchronously in-process.
CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", default=not REDIS_URL)
CELERY_TASK_IGNORE_RESULT = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "evaluate-alerts": {
        "task": "alerts.tasks.evaluate_alerts",
        "schedule": timedelta(seconds=env_int("NIGHTWATCH_ALERTS_INTERVAL_SECONDS", 30)),
    },
    "prune-old-data": {
        "task": "metrics.tasks.prune_old_data",
        "schedule": timedelta(hours=1),
    },
    "simulate-demo-servers": {
        "task": "metrics.tasks.simulate_demo_servers",
        "schedule": timedelta(seconds=15),
    },
}

# --- NightWatch -------------------------------------------------------------------

# A server is considered offline when the agent has not reported for this long.
NIGHTWATCH_OFFLINE_AFTER = timedelta(seconds=env_int("NIGHTWATCH_OFFLINE_AFTER_SECONDS", 120))
# DRF rate string for the ingest endpoint, applied per server (not per IP).
NIGHTWATCH_INGEST_RATE = os.environ.get("NIGHTWATCH_INGEST_RATE", "30/min")
NIGHTWATCH_METRICS_RETENTION_DAYS = env_int("NIGHTWATCH_METRICS_RETENTION_DAYS", 30)
NIGHTWATCH_LOGS_RETENTION_DAYS = env_int("NIGHTWATCH_LOGS_RETENTION_DAYS", 14)
# Keeps demo servers alive with fresh synthetic metrics (Celery beat task).
NIGHTWATCH_DEMO_MODE = env_bool("NIGHTWATCH_DEMO_MODE", default=False)
# Public base URL used in the agent install command; falls back to the request host.
NIGHTWATCH_PUBLIC_URL = os.environ.get("NIGHTWATCH_PUBLIC_URL", "").rstrip("/")
# Where the agent files served by /agent/<file> live (repo layout and Docker image match).
NIGHTWATCH_AGENT_DIR = Path(os.environ.get("NIGHTWATCH_AGENT_DIR", REPO_DIR / "agent"))
NIGHTWATCH_DEPLOY_DIR = Path(os.environ.get("NIGHTWATCH_DEPLOY_DIR", REPO_DIR / "deploy"))

# --- Telegram ------------------------------------------------------------------------

# Token from @BotFather. Empty = bot and notifications are disabled (everything else works).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Bot username without @, used for the t.me deep link on the "link chat" page.
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@")

# --- Logging ------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
    },
}
