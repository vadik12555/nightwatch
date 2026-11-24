#!/bin/sh
# Container entrypoint. One-off setup runs only where it is enabled by env
# (the web container), so the Celery container never races it on migrations.
set -e

if [ "${DJANGO_MIGRATE:-0}" = "1" ]; then
    echo "Applying database migrations..."
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput --verbosity 0
    python manage.py ensure_admin
fi

if [ "${DJANGO_SEED_DEMO:-0}" = "1" ]; then
    python manage.py seed_demo --if-empty
fi

exec "$@"
