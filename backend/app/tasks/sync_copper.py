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
    """Reconcile the dashboard with Copper's current "open leads assigned to me":

      1. IMPORT  — any open-assigned Copper lead we don't have yet is created
                   and queued for assessment (covers leads newly assigned to us).
      2. ARCHIVE — any ACTIVE local lead whose copper_id is no longer in that
                   set (reassigned away / closed / status changed externally)
                   is archived locally, so the board mirrors Copper.

    Reconciliation never touches leads without a copper_id (manual/test leads,
    or leads we converted to opportunities — their copper_id is nulled), and it
    never un-archives (dedup/rejection archives are deliberate human outcomes).
    """
    raw_leads = fetch_open_leads_for_user()
    if settings.test_lead_limit > 0:
        raw_leads = raw_leads[: settings.test_lead_limit]

    # Safety valve: an empty fetch is far more likely a Copper hiccup or auth
    # problem than the manager genuinely unassigning every single lead. Refuse
    # to mass-archive the whole board on that signal.
    if not raw_leads:
        print("[sync_copper] Copper returned 0 open leads — skipping reconcile (safety)")
        return {"skipped": "copper returned 0 leads"}

    copper_ids = {str(r.get("id", "")) for r in raw_leads if r.get("id")}

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

        # Archive active leads that are no longer open-assigned to us in Copper.
        stale_count = 0
        result = await db.execute(
            select(Lead).where(Lead.copper_id.is_not(None), Lead.status != "archived")
        )
        for lead in result.scalars().all():
            if lead.copper_id in copper_ids:
                continue
            lead.status = "archived"
            stale_count += 1
            try:
                from app.services.events import log_event, EVENT_ARCHIVED
                await log_event(db, lead.id, EVENT_ARCHIVED,
                                {"reason": "copper_reconcile: no longer open-assigned to user"})
            except Exception as exc:
                print(f"[sync_copper] event log skipped for {lead.id}: {exc!r}")

        await db.commit()
        print(f"[sync_copper] imported={new_count} archived_stale={stale_count} copper_open={len(raw_leads)}")
        return {"synced": new_count, "archived_stale": stale_count, "checked": len(raw_leads)}
