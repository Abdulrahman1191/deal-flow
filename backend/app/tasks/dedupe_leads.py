"""
Scheduled lead de-duplication.

Runs the shared dedup core (app.services.dedup.dedupe_leads) on the worker's beat
schedule so the board stays free of duplicate-name leads automatically — no manual
command needed. Commits (archives duplicates); the action is reversible and
logged as a lead_event, and archived rows keep their copper_id so Copper sync
won't re-import them.
"""
from __future__ import annotations

import asyncio

from app.database import CelerySessionLocal
from app.services.dedup import dedupe_leads
from app.tasks.celery_app import celery


@celery.task(bind=True, max_retries=2, default_retry_delay=120)
def dedupe_leads_task(self) -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


async def _run() -> dict:
    async with CelerySessionLocal() as db:
        report = await dedupe_leads(db, commit=True)
        print(f"[dedupe_leads] active={report['active']} groups={report['groups']} "
              f"archived={report['archived']}")
        # keep the response light (don't ship the full detail list back through Redis)
        return {k: report[k] for k in ("active", "groups", "to_archive", "archived")}
