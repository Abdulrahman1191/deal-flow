from __future__ import annotations
"""
Auto-recovery for leads dead-lettered to 'failed' (issue #163).

assess_lead_task dead-letters a lead to 'failed' once MAX_ASSESS_ATTEMPTS is
exceeded (issue #129, see app/tasks/assess_lead.py) or a caught exception
survives every Celery retry -- and, unlike 'processing'/'pending', nothing
else ever re-queues it: reap_stuck_leads.py's REAP_STATUSES explicitly
excludes 'failed' so a poison lead can't crash-loop the worker forever. That
leaves a 'failed' lead stuck there permanently even when the underlying cause
was transient (a scrape timeout, a flaky DeepSeek call, a since-fixed data
issue) -- exactly what stranded 367 leads firm-wide with no way to recover
short of a manual reassess per lead.

This periodic task re-queues 'failed' leads whose last_assessment_error_at is
older than REDRIVE_AFTER_HOURS, resetting assessment_attempts so
MAX_ASSESS_ATTEMPTS doesn't immediately dead-letter them again without even
running. A null last_assessment_error_at (a 'failed' lead from before this
column existed, or one dead-lettered by a code path that predates it) is
treated as eligible immediately, same as reap_stuck_leads treats a null
updated_at as stale -- these are exactly the pre-existing stuck leads this
task exists to unstick.

Bounded by leads.assessment_failed_redrives against
settings.assessment_failed_max_redrives (default 3, tracked per lead) so a
lead broken by something this task can't fix (a permanently bad payload, a
dead website) doesn't loop forever -- past the cap it's left 'failed' for
good, still visible via GET /api/v1/leads/failed-summary. The cap is
enforced at the SQL level (not just skipped in Python after fetching), so a
batch of permanently-dead leads can't occupy the query's working set forever
and starve newer redrivable failures behind them -- mirrors
app/tasks/redrive_outbox.py's redrive_count guard.

Only 'failed' leads are ever touched, which also means an archived lead is
never redriven even if it once passed through 'failed' -- 'status' is a
single column, so a lead currently 'archived' cannot also match
status == 'failed'.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.tasks.assess_lead import assess_lead_task
from app.tasks.celery_app import celery

BATCH_SIZE = 100
REDRIVE_AFTER_HOURS = 6
DEFAULT_MAX_REDRIVES = 3


def _is_eligible(last_error_at: Optional[datetime], cutoff: datetime) -> bool:
    if last_error_at is None:
        return True
    if last_error_at.tzinfo is None:
        last_error_at = last_error_at.replace(tzinfo=timezone.utc)
    return last_error_at < cutoff


async def _run() -> dict:
    configured = settings.assessment_failed_max_redrives
    max_redrives = configured if configured and configured > 0 else DEFAULT_MAX_REDRIVES
    cutoff = datetime.now(timezone.utc) - timedelta(hours=REDRIVE_AFTER_HOURS)

    redriven = 0

    async with CelerySessionLocal() as db:
        result = await db.execute(
            select(Lead)
            .where(Lead.status == "failed")
            .where(Lead.assessment_failed_redrives < max_redrives)
            .order_by(Lead.last_assessment_error_at.asc().nullsfirst())
            .limit(BATCH_SIZE)
        )
        candidates = result.scalars().all()
        eligible = [lead for lead in candidates if _is_eligible(lead.last_assessment_error_at, cutoff)]

        for lead in eligible:
            lead.status = "pending"
            lead.assessment_attempts = 0
            lead.assessment_failed_redrives += 1
            assess_lead_task.delay(str(lead.id))
            redriven += 1
            print(
                f"[redrive_failed_assessments] re-queued lead={lead.id} "
                f"(redrive #{lead.assessment_failed_redrives})"
            )

        if eligible:
            await db.commit()

    result_summary = {"checked": len(candidates), "redriven": redriven}
    print(f"[redrive_failed_assessments] {result_summary}")
    return result_summary


@celery.task(bind=True, max_retries=2, default_retry_delay=120)
def redrive_failed_assessments_task(self) -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
