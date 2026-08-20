from __future__ import annotations
"""
Periodic re-drive for copper_outbox rows that exhausted drain_outbox's 5
delivery attempts and landed in status='failed' (issue #131). Nothing else
ever resets a failed row's status, so without this task a transient Copper
outage (or any other run of bad luck across all 5 attempts) permanently
strands that write-back and the CRM silently drifts.

This complements the durable outbox rather than replacing it: worker-down is
already handled by drain_copper_outbox_task being crash-safe (attempts only
increment on a real delivery failure, never on the worker just not running
yet) -- this task covers the case where delivery was actually attempted and
failed every time.

Bounded by two independent guards so this can't loop a genuinely-dead row
forever or keep hammering a record Copper will never accept:
- redrive_count / settings.outbox_max_redrives: each redrive increments the
  row's redrive_count; once it reaches the cap the row is left 'failed' for
  good, still visible via GET /leads/outbox-health (issue #65).
- _is_terminal_error(last_error): a 404/not-found or other non-retryable
  4xx (excluding 408/429, which are transient) means the identical request
  can never succeed no matter how many times it's retried, so the row is
  skipped outright and doesn't count against the cap.

Only 'failed' rows are ever touched -- 'pending' rows are already live in
drain_copper_outbox_task's queue, and 'done' rows are finished.
"""
import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.database import CelerySessionLocal
from app.models.copper_outbox import CopperOutbox
from app.tasks.celery_app import celery

BATCH_SIZE = 50
DEFAULT_MAX_REDRIVES = 3

# Status codes where an identical retry could plausibly succeed later (rate
# limited, timed out) -- every other 4xx is a client error that retrying the
# exact same request will never fix (bad payload, unauthorized, gone, ...).
_TRANSIENT_CLIENT_STATUS = {408, 429}
# Matches the status code out of httpx's raise_for_status() message, e.g.
# "Client error '404 Not Found' for url '...'".
_STATUS_RE = re.compile(r"'(\d{3})[ A-Za-z]")


def _is_terminal_error(last_error: Optional[str]) -> bool:
    """True when last_error indicates a write that can never succeed, no
    matter how many more times it's retried."""
    if not last_error:
        return False
    match = _STATUS_RE.search(last_error)
    if match:
        code = int(match.group(1))
        return 400 <= code < 500 and code not in _TRANSIENT_CLIENT_STATUS
    return "not found" in last_error.lower()


async def _run() -> dict:
    configured = settings.outbox_max_redrives
    max_redrives = configured if configured and configured > 0 else DEFAULT_MAX_REDRIVES
    now = datetime.now(timezone.utc)

    redriven = skipped_terminal = skipped_capped = 0

    async with CelerySessionLocal() as db:
        result = await db.execute(
            select(CopperOutbox)
            .where(CopperOutbox.status == "failed")
            .order_by(CopperOutbox.created_at.asc())
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        rows = result.scalars().all()

        for row in rows:
            if _is_terminal_error(row.last_error):
                skipped_terminal += 1
                continue
            if row.redrive_count >= max_redrives:
                skipped_capped += 1
                continue

            row.status = "pending"
            row.attempts = 0
            row.next_attempt_at = now
            row.redrive_count += 1
            row.updated_at = now
            redriven += 1
            print(f"[redrive_outbox] re-driving {row.endpoint} (redrive #{row.redrive_count})")

        await db.commit()

    result_summary = {
        "checked": len(rows),
        "redriven": redriven,
        "skipped_terminal": skipped_terminal,
        "skipped_capped": skipped_capped,
    }
    print(f"[redrive_outbox] {result_summary}")
    return result_summary


@celery.task(name="app.tasks.redrive_outbox.redrive_failed_outbox_task")
def redrive_failed_outbox_task() -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
