from __future__ import annotations
"""
Reaper: recovers leads orphaned by a Celery worker crash mid-assessment (issue #100).

assess_lead_task flips a lead to 'processing' at the start (app/tasks/assess_lead.py)
and 'assessed' at the end. If the worker dies in between -- restart, deploy, OOM --
and the task isn't redelivered, the lead is stuck in 'processing' forever: no
bucket, invisible to a partner's board. The same thing happens to a 'pending'
lead whose task was never picked up at all (e.g. dropped during a broker
restart before assess_lead_task.delay() reached a worker).

This periodic task (see celery_app.py beat_schedule) finds leads in either
status whose updated_at is older than settings.assessment_reap_after_minutes
and re-enqueues assess_lead_task for each. The threshold must stay comfortably
above a normal assessment's runtime so a genuinely in-flight lead is never
double-run -- assess_lead._run() re-fetches the lead and upserts its
AssessmentCard rather than duplicating it, so a duplicate run is safe either
way, just wasted work. assess_lead_task itself is also acks_late (crash-safety
backstop): this reaper exists for the cases that slip past redelivery, e.g. a
broker restart that drops an unacked task outright.

Each reaped lead's updated_at is bumped to now() and committed in the same
pass, so it isn't stale again -- and therefore isn't re-enqueued -- until a
full reap window has elapsed. Without this, a lead still sitting unstarted in
the broker queue at the next beat looks exactly as stale as before and gets
re-enqueued again every cycle: with hundreds of leads and multi-minute
assessments, the queue can't drain within one beat interval, so every
subsequent beat would pile on another full round of duplicate tasks. If the
re-enqueued task itself is lost, the bumped updated_at still ages past the
window and the lead is reaped again, so self-healing is preserved.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.tasks.assess_lead import assess_lead_task
from app.tasks.celery_app import celery

REAP_STATUSES = ("processing", "pending")


def _is_stale(updated_at: datetime | None, cutoff: datetime) -> bool:
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at < cutoff


async def _run() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.assessment_reap_after_minutes)

    async with CelerySessionLocal() as db:
        result = await db.execute(select(Lead).where(Lead.status.in_(REAP_STATUSES)))
        leads = result.scalars().all()

        stale = [lead for lead in leads if _is_stale(lead.updated_at, cutoff)]

        counts = {"processing": 0, "pending": 0}
        for lead in stale:
            counts[lead.status] = counts.get(lead.status, 0) + 1
            assess_lead_task.delay(str(lead.id))
            lead.updated_at = datetime.now(timezone.utc)
            print(f"[reap_stuck_leads] re-enqueued lead={lead.id} status={lead.status}")

        if stale:
            await db.commit()

    result_summary = {"checked": len(leads), "reaped": len(stale), **counts}
    print(f"[reap_stuck_leads] {result_summary}")
    return result_summary


@celery.task(bind=True, max_retries=2, default_retry_delay=120)
def reap_stuck_leads_task(self) -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
