from __future__ import annotations
import asyncio

from sqlalchemy import select

from app.config import settings
from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.services.copper_service import fetch_open_leads_for_user, map_copper_lead
from app.tasks.celery_app import celery
from app.tasks.assess_lead import assess_lead_task


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def sync_copper_leads_task(self) -> dict:
    import os
    if os.getenv("DISABLE_COPPER_SYNC", "").lower() in ("1", "true", "yes"):
        return {"skipped": "DISABLE_COPPER_SYNC env var is set"}
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run() -> dict:
    raw_leads = fetch_open_leads_for_user()
    if settings.test_lead_limit > 0:
        raw_leads = raw_leads[: settings.test_lead_limit]

    async with CelerySessionLocal() as db:
        new_count = 0
        for raw in raw_leads:
            copper_id = str(raw.get("id", ""))
            if not copper_id:
                continue

            existing = await db.execute(select(Lead).where(Lead.copper_id == copper_id))
            if existing.scalar_one_or_none():
                continue

            lead_data = map_copper_lead(raw)
            lead = Lead(**lead_data)
            db.add(lead)
            await db.flush()

            assess_lead_task.delay(str(lead.id))
            new_count += 1

        await db.commit()
        return {"synced": new_count, "checked": len(raw_leads)}
