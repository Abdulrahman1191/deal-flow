"""
One-time backfill: applies issue #170's new awaiting_deck rules retroactively
to leads already parked there under the old (5-day grace, no promotion cap)
behaviour.

For every current awaiting_deck lead, one of three actions:
  queue             -- email-sourced (Copper "Source detail" marks it as
                        inbound email to the application inbox) or already
                        past the shortened settings.deck_grace_period_days --
                        queues assess_lead_task, which now applies the #170
                        rules itself (email-subject context, promotion cap).
  maybe_placeholder -- has already cycled through awaiting_deck more times
                        than settings.max_deck_promotions AND has no deck, no
                        substantial description, no website, and no usable
                        email-subject content (all locally verifiable without
                        a network fetch) -- writes the MAYBE placeholder card
                        directly instead of queuing yet another doomed cycle.
  none              -- neither applies (freshly parked, still within grace,
                        no email-sourced signal) -- left alone.

"Already cycled" is estimated from how many `awaiting_deck` LeadEvents exist
for the lead (one per park: the initial import park, plus one per
promote_awaiting_deck.py re-park) rather than leads.deck_promotion_count,
since that column is brand new and starts at 0 for every existing row.

Dry-run by default: prints the plan, makes NO writes. --commit applies it.
Per-lead success/failure is logged; one failure does not abort the rest.
Idempotent: a second run only ever finds leads still in `awaiting_deck` --
once queued or placeholder'd, a lead's status moves off awaiting_deck (to
'processing'/'assessed'), so it drops out of the candidate set.

Usage (from backend/):
  python scripts/rebalance_awaiting_deck.py            # dry run
  python scripts/rebalance_awaiting_deck.py --commit

Reads DATABASE_URL from the environment (same as the app/worker).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.event import LeadEvent
from app.models.lead import Lead
from app.services.copper_service import get_custom_field_value
from app.tasks.assess_lead import (
    MIN_DESCRIPTION_CHARS,
    _extract_source_detail_subject,
    assess_lead_task,
    write_no_context_maybe_placeholder,
)
from app.tasks.sync_copper import _is_email_sourced


# --- Pure planning step (no DB/network access) --------------------------------

def _has_local_context(lead: Lead) -> bool:
    """Whatever assess_lead._run can determine about this lead WITHOUT a
    network fetch: deck text, a substantial description, a usable
    Source-detail subject, or a website worth scraping. A non-empty website
    counts as "possibly has context" here -- we can't know offline whether a
    scrape would succeed, so this stays conservative: it never placeholders a
    lead a real assessment might still be able to score."""
    if lead.pitch_deck_text:
        return True
    if lead.website:
        return True
    if lead.description and len(lead.description.strip()) >= MIN_DESCRIPTION_CHARS:
        return True
    subject = _extract_source_detail_subject(
        get_custom_field_value(lead.raw_copper_data, settings.copper_cf_source_detail_id)
    )
    return bool(subject and len(subject) >= MIN_DESCRIPTION_CHARS)


def plan_rebalance(rows: Iterable[dict]) -> dict:
    """rows: [{"lead": Lead, "park_count": int}, ...] for every current
    awaiting_deck lead, where park_count is how many "awaiting_deck"
    LeadEvents exist for that lead so far. Returns the three action buckets
    plus a summary count. Pure -- no DB/network access, so it's cheap to
    unit-test against hand-built rows."""
    grace_cutoff = datetime.now(timezone.utc) - timedelta(days=settings.deck_grace_period_days)

    queue: list = []
    maybe_placeholder: list = []
    none: list = []

    for row in rows:
        lead = row["lead"]
        park_count = row["park_count"]
        # The very first park (at import time) isn't a "promotion" --
        # promotions are re-parks issued by promote_awaiting_deck.py.
        promotions_so_far = max(0, park_count - 1)
        email_sourced = _is_email_sourced(lead.raw_copper_data)
        wait_started = lead.deck_wait_started_at or lead.created_at
        past_grace = bool(wait_started and wait_started < grace_cutoff)

        summary_row = {
            "id": str(lead.id),
            "company_name": lead.company_name,
            "owner_email": lead.owner_email,
            "park_count": park_count,
            "email_sourced": email_sourced,
            "past_grace": past_grace,
        }

        if promotions_so_far > settings.max_deck_promotions and not _has_local_context(lead):
            maybe_placeholder.append(summary_row)
        elif email_sourced or past_grace:
            queue.append(summary_row)
        else:
            none.append(summary_row)

    return {
        "queue": queue,
        "maybe_placeholder": maybe_placeholder,
        "none": none,
        "counts": {
            "queue": len(queue),
            "maybe_placeholder": len(maybe_placeholder),
            "none": len(none),
        },
    }


# --- DB access ----------------------------------------------------------------

async def _fetch_rows(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(Lead).where(Lead.status == "awaiting_deck"))
    leads = list(result.scalars().all())
    if not leads:
        return []

    lead_ids = [lead.id for lead in leads]
    event_result = await db.execute(
        select(LeadEvent.lead_id, func.count())
        .where(LeadEvent.lead_id.in_(lead_ids), LeadEvent.event_type == "awaiting_deck")
        .group_by(LeadEvent.lead_id)
    )
    park_counts = dict(event_result.all())
    return [{"lead": lead, "park_count": park_counts.get(lead.id, 0)} for lead in leads]


async def apply_rebalance(db: AsyncSession, rows: list[dict], plan: dict) -> dict:
    """Applies the plan: queues assess_lead_task for `queue` rows, writes the
    MAYBE placeholder directly for `maybe_placeholder` rows. Per-lead
    try/except so one failure never aborts the rest."""
    leads_by_id = {str(row["lead"].id): row["lead"] for row in rows}
    result = {
        "queued_ok": [], "queued_failed": [],
        "placeholder_ok": [], "placeholder_failed": [],
    }

    for entry in plan["queue"]:
        lead_id = entry["id"]
        try:
            assess_lead_task.delay(lead_id)
            result["queued_ok"].append(lead_id)
            print(f"  OK    queued: {entry['company_name']}  [id={lead_id}]")
        except Exception as exc:
            result["queued_failed"].append(lead_id)
            print(f"  FAIL  queue: {entry['company_name']}  [id={lead_id}] -- {exc!r}")

    for entry in plan["maybe_placeholder"]:
        lead_id = entry["id"]
        lead = leads_by_id[lead_id]
        try:
            await write_no_context_maybe_placeholder(db, lead)
            result["placeholder_ok"].append(lead_id)
            print(f"  OK    MAYBE placeholder: {entry['company_name']}  [id={lead_id}]")
        except Exception as exc:
            result["placeholder_failed"].append(lead_id)
            print(f"  FAIL  MAYBE placeholder: {entry['company_name']}  [id={lead_id}] -- {exc!r}")

    return result


# --- Rendering ------------------------------------------------------------------

def render_plan(plan: dict) -> str:
    lines = [
        "Rebalance awaiting_deck leads under issue #170's rules",
        f"=== Queue for (re-)assessment: {len(plan['queue'])} ===",
    ]
    for row in plan["queue"]:
        reason = "email-sourced" if row["email_sourced"] else "past grace"
        lines.append(f"  - {row['company_name']}  [id={row['id']}] ({reason}, parked {row['park_count']}x)")
    lines.append("")
    lines.append(f"=== MAYBE placeholder (past promotion cap, no local context): {len(plan['maybe_placeholder'])} ===")
    for row in plan["maybe_placeholder"]:
        lines.append(f"  - {row['company_name']}  [id={row['id']}] (parked {row['park_count']}x)")
    lines.append("")
    lines.append(f"=== Left alone (still within grace, no signal): {len(plan['none'])} ===")
    for row in plan["none"]:
        lines.append(f"  - {row['company_name']}  [id={row['id']}]")
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------

async def _run(commit: bool) -> int:
    async with AsyncSessionLocal() as db:
        rows = await _fetch_rows(db)
        plan = plan_rebalance(rows)
        print(render_plan(plan))

        total = len(plan["queue"]) + len(plan["maybe_placeholder"])
        if not commit:
            print(f"\nDRY RUN -- {total} lead(s) would be touched. Re-run with --commit to apply.")
            return 0

        if total == 0:
            print("\nNothing to do.")
            return 0

        print(f"\nApplying to {total} lead(s)...")
        result = await apply_rebalance(db, rows, plan)
        failed = len(result["queued_failed"]) + len(result["placeholder_failed"])
        print(
            f"\nDone. {len(result['queued_ok'])} queued, "
            f"{len(result['placeholder_ok'])} placeholder'd, "
            f"{failed} failed."
        )
        return 1 if failed else 0


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true",
                         help="Apply the writes. Without this flag, dry-run only (no writes).")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.commit))


if __name__ == "__main__":
    sys.exit(main())
