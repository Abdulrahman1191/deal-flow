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

Two guards keep a single run from doing something silly:
- REAP_BATCH_LIMIT caps how many leads one pass re-enqueues, oldest-first, so
  a large backlog (e.g. after an extended outage) doesn't dump a thundering
  herd on the workers all at once -- the remainder just waits for the next
  beat.
- A misconfigured assessment_reap_after_minutes of 0 (or unset, which
  pydantic would coerce from an empty env var) would otherwise make *every*
  pending/processing lead -- including ones queued a second ago -- look
  stale. Falls back to DEFAULT_REAP_AFTER_MINUTES instead.

A lead that assess_lead_task has dead-lettered to 'failed' after exceeding
MAX_ASSESS_ATTEMPTS (issue #129, see app/tasks/assess_lead.py) is never
re-enqueued by this reaper: REAP_STATUSES only matches 'processing'/'pending',
so a 'failed' lead falls straight out of the query above. This is what keeps
a poison lead from being re-armed into another crash loop.
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
REAP_BATCH_LIMIT = 100
DEFAULT_REAP_AFTER_MINUTES = 30


def _is_stale(updated_at: datetime | None, cutoff: datetime) -> bool:
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at < cutoff


async def _run() -> dict:
    configured = settings.assessment_reap_after_minutes
    reap_after_minutes = configured if configured and configured > 0 else DEFAULT_REAP_AFTER_MINUTES
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=reap_after_minutes)

    async with CelerySessionLocal() as db:
        result = await db.execute(select(Lead).where(Lead.status.in_(REAP_STATUSES)))
        leads = result.scalars().all()

        stale = [lead for lead in leads if _is_stale(lead.updated_at, cutoff)]
        # Oldest (and null, treated as oldest) first, so a capped run always
        # clears the longest-orphaned leads before newer arrivals.
        stale.sort(key=lambda lead: lead.updated_at or datetime.min.replace(tzinfo=timezone.utc))
        capped = stale[:REAP_BATCH_LIMIT]

        counts = {"processing": 0, "pending": 0}
        for lead in capped:
            counts[lead.status] = counts.get(lead.status, 0) + 1
            assess_lead_task.delay(str(lead.id))
            lead.updated_at = datetime.now(timezone.utc)
            print(f"[reap_stuck_leads] re-enqueued lead={lead.id} status={lead.status}")

        if capped:
            await db.commit()

    if len(stale) > len(capped):
        print(f"[reap_stuck_leads] batch cap reached: {len(stale)} stale, only {len(capped)} re-enqueued this run")

    result_summary = {"checked": len(leads), "reaped": len(capped), **counts}
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
