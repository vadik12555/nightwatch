"""Celery application.

Workers and beat are started from this module:
    celery -A config worker -B -l info
Task modules are discovered automatically in every installed app (`<app>/tasks.py`).
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("nightwatch")
# All CELERY_* settings from Django settings configure Celery.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
