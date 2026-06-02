from __future__ import annotations
"""
Drains the copper_outbox table: picks up pending rows whose next_attempt_at has
passed, makes the Copper API call, and marks them done or schedules a retry.

Backoff schedule (seconds): 30, 60, 120, 240, 480 → gives up after 5 attempts (~15 min total).
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import CelerySessionLocal
from app.models.copper_outbox import CopperOutbox
from app.services.copper_writer import execute_copper_request, MAX_ATTEMPTS, _BACKOFF_SECONDS
from app.tasks.celery_app import celery

BATCH_SIZE = 20


@celery.task(name="app.tasks.drain_outbox.drain_copper_outbox_task")
def drain_copper_outbox_task() -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_drain())
    finally:
        loop.close()


async def _drain() -> dict:
    now = datetime.now(timezone.utc)
    done = failed = retried = 0

    async with CelerySessionLocal() as db:
        result = await db.execute(
            select(CopperOutbox)
            .where(CopperOutbox.status == "pending")
            .where(CopperOutbox.next_attempt_at <= now)
            .order_by(CopperOutbox.created_at.asc())
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        rows = result.scalars().all()

        for row in rows:
            try:
                execute_copper_request(row.endpoint, row.method, row.body_json)
                row.status = "done"
                row.updated_at = now
                done += 1
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)[:500]
                row.updated_at = now

                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                    failed += 1
                    print(f"[drain_outbox] GAVE UP on {row.endpoint} after {row.attempts} attempts: {exc!r}")
                else:
                    delay = _BACKOFF_SECONDS[min(row.attempts - 1, len(_BACKOFF_SECONDS) - 1)]
                    row.next_attempt_at = now + timedelta(seconds=delay)
                    retried += 1
                    print(f"[drain_outbox] retry #{row.attempts} for {row.endpoint} in {delay}s")

        await db.commit()

    return {"done": done, "retried": retried, "failed": failed}
