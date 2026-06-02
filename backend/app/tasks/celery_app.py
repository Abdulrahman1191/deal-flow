from __future__ import annotations
from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "raedventures",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.assess_lead", "app.tasks.generate_briefing", "app.tasks.sync_copper", "app.tasks.drain_outbox"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "daily-briefing": {
            "task": "app.tasks.generate_briefing.generate_briefing_task",
            "schedule": crontab(hour=settings.briefing_cron_hour, minute=settings.briefing_cron_minute),
        },
        # Disabled in test env to avoid re-importing manually pruned leads.
        # Re-enable for prod by uncommenting.
        # "sync-copper-leads": {
        #     "task": "app.tasks.sync_copper.sync_copper_leads_task",
        #     "schedule": 300.0,  # every 5 minutes
        # },
        "drain-copper-outbox": {
            "task": "app.tasks.drain_outbox.drain_copper_outbox_task",
            "schedule": 30.0,  # every 30 seconds
        },
    },
)
