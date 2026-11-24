# Backend image: Django app served by Gunicorn. Also used for the Celery worker.
# Build context is the repository root (the image ships the agent files for /agent/<file>).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Unprivileged runtime user.
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

COPY --chown=app:app backend/ /app/
COPY --chown=app:app agent/ /agent/
COPY --chown=app:app deploy/nightwatch-agent.service /deploy/nightwatch-agent.service
# staticfiles/ is a named volume shared with Nginx; it must exist and belong to "app".
RUN mkdir -p /app/staticfiles && chown app:app /app/staticfiles && chmod +x /app/entrypoint.sh

USER app
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", \
     "--access-logfile", "-", "--forwarded-allow-ips", "*"]
