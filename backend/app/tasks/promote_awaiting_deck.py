from __future__ import annotations
"""
Grace-period fallback for leads parked in `awaiting_deck` (issue #149).

A brand-new deck-less lead is parked in `awaiting_deck` at import time
(app/tasks/sync_copper.py) without being assessed, so a deck that's about to
be uploaded to the Drive folder gets used instead of a premature deck-less
verdict. The Drive/Copper-link sweeps in sync_pitch_decks.py auto-attach a
deck and queue the real, deck-backed assessment if one shows up within
settings.deck_grace_period_days.

If nothing shows up in time, this periodic task re-queues assess_lead_task
for the lead so it falls through to the #144 website/description assessment
path instead of staying parked forever -- assess_lead._run's own gate still
re-parks it in awaiting_deck if there's genuinely no usable website or
description content either.

Runs a few times a day (see the "promote-awaiting-deck" beat schedule in
celery_app.py) -- there's no need to poll more often than that for a
day-granularity grace period, and it keeps this off the hot path of the
30-minute deck sweeps.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.tasks.assess_lead import assess_lead_task
from app.tasks.celery_app import celery


async def _run() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.deck_grace_period_days)
    promoted = 0

    async with CelerySessionLocal() as db:
        leads = await _fetch_stale_leads(db, cutoff)
        for lead in leads:
            # Reset the clock on every promotion attempt so a lead that gets
            # re-parked in awaiting_deck (still no usable context) isn't
            # re-promoted again on the very next beat tick -- it gets another
            # full grace period before this task looks at it again.
            lead.deck_wait_started_at = datetime.now(timezone.utc)
            await db.commit()
            try:
                assess_lead_task.delay(str(lead.id))
                promoted += 1
            except Exception as exc:
                print(f"[promote_awaiting_deck] enqueue failed for lead={lead.id}: {exc!r}")

    result = {"checked": len(leads), "promoted": promoted}
    print(f"[promote_awaiting_deck] {result}")
    return result


async def _fetch_stale_leads(db: AsyncSession, cutoff: datetime) -> list[Lead]:
    """awaiting_deck leads whose wait began before `cutoff`. Falls back to
    created_at when deck_wait_started_at is null -- e.g. a lead that entered
    awaiting_deck some other way than the brand-new-import path -- so it
    still ages out instead of waiting forever."""
    result = await db.execute(
        select(Lead).where(
            Lead.status == "awaiting_deck",
            func.coalesce(Lead.deck_wait_started_at, Lead.created_at) < cutoff,
        )
    )
    return list(result.scalars().all())


@celery.task(bind=True, max_retries=3, default_retry_delay=120)
def promote_stale_awaiting_deck_task(self) -> dict:
    """Beat task: past-grace-period deck-less leads fall back to the #144
    website/description assessment instead of staying parked forever."""
    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)
