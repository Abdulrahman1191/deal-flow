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
- _is_terminal_error(last_error): a 404/not-found, other non-retryable 4xx
  (excluding 408/429, which are transient), or a _record_skipped_write()
  row (last_error starts with "skipped " -- the write was never attempted
  because a required config id is unset, so body_json is {} and re-driving
  could never perform the intended write) means the identical request can
  never succeed no matter how many times it's retried.

Rows that hit either guard are stamped redrive_count = max_redrives right
away (not just skipped in Python) and the query's WHERE clause excludes
anything at/over the cap. Without both halves of that, a batch of 50
permanently-dead rows (nothing ever purges outbox rows) would sit at the
front of the created_at-ordered query forever -- every run would re-check
the same 50 dead rows, redrive nothing, and starve newer redrivable
failures indefinitely.

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
    if last_error.startswith("skipped "):
        # _record_skipped_write() (copper_writer.py) inserts these directly
        # as 'failed' with body_json={} purely so GET /leads/outbox-health
        # (#65) surfaces a config problem -- the write was never attempted,
        # so there's no payload to re-drive. Resetting one to 'pending'
        # would issue a real empty PUT; if that happens to succeed the row
        # flips to 'done' and the config-unset signal silently disappears.
        return True
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
            # Excludes permanently-dead rows from the working set itself --
            # without this, once BATCH_SIZE dead rows accumulate they'd sit
            # at the front of this created_at-ordered query forever, since
            # nothing else ever purges/updates a skipped row's status.
            .where(CopperOutbox.redrive_count < max_redrives)
            .order_by(CopperOutbox.created_at.asc())
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        rows = result.scalars().all()

        for row in rows:
            if _is_terminal_error(row.last_error):
                skipped_terminal += 1
                # Stamp the row so the WHERE clause above excludes it on
                # every future run too -- otherwise a terminal row (which
                # never advances past this check) would occupy a batch slot
                # forever, same starvation this task exists to prevent.
                row.redrive_count = max_redrives
                row.updated_at = now
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
