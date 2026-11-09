"""Celery app: one queue, separate workers for detection jobs (FRD 11, FR-080).

    celery -A minefoot_web worker -l info --concurrency 1
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "minefoot_web.settings")
app = Celery("minefoot_web")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
