"""
One-time backfill: leads that were auto-rejected (or otherwise scored) for
lack of a pitch deck are moved into the `awaiting_deck` holding status
introduced by issue #67, so they leave every teammate's board until a deck is
attached. Once a deck lands (via sync_pitch_decks_task or the per-lead
POST /leads/{id}/sync-pitch-deck), the existing re-assessment flow scores the
lead normally and it rejoins the board.

Owner-agnostic: scans every account's leads, not just settings.owner_email —
mirrors close_stale_copper.py's --commit/dry-run shape but has no per-owner
scoping since this must apply firm-wide.

Dry-run by default: prints the plan (candidate counts per owner), makes NO
writes. --commit applies it. Idempotent: a lead already in `awaiting_deck`,
or any lead with pitch_deck_text set, never matches, so a second run finds
nothing to do.

Usage (from backend/):
  python scripts/backfill_awaiting_deck.py            # dry run
  python scripts/backfill_awaiting_deck.py --commit

Reads DATABASE_URL from the environment (same as the app/worker).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.services.events import EVENT_AWAITING_DECK, log_event

# Never touch archived/approved (out of the running either way) or leads
# already parked (idempotency).
EXCLUDED_STATUSES = ("archived", "approved", "awaiting_deck")


# --- Pure planning step (no DB access) --------------------------------------

def plan_backfill(leads: Iterable) -> dict:
    """leads: candidate rows already filtered by the DB query below (no
    pitch_deck_text, not archived/approved/awaiting_deck). Returns the plan
    plus a per-owner count for the printed summary."""
    rows = [{"id": str(lead.id), "company_name": lead.company_name, "owner_email": lead.owner_email} for lead in leads]
    counts = Counter(row["owner_email"] or "(no owner)" for row in rows)
    return {"leads": rows, "counts": dict(sorted(counts.items()))}


# --- DB access ---------------------------------------------------------------

async def _fetch_candidates(db: AsyncSession) -> list[Lead]:
    result = await db.execute(
        select(Lead).where(
            Lead.status.notin_(EXCLUDED_STATUSES),
            or_(Lead.pitch_deck_text.is_(None), Lead.pitch_deck_text == ""),
        )
    )
    return list(result.scalars().all())


async def apply_backfill(db: AsyncSession, leads: list[Lead]) -> int:
    for lead in leads:
        lead.status = "awaiting_deck"
        await log_event(db, lead.id, EVENT_AWAITING_DECK, {"reason": "backfill"})
    await db.commit()
    return len(leads)


# --- Rendering ---------------------------------------------------------------

def render_plan(plan: dict) -> str:
    lines = [
        "Backfill: park deckless leads into 'awaiting_deck' (all owners)",
        f"=== Candidates: {len(plan['leads'])} ===",
    ]
    for owner, count in plan["counts"].items():
        lines.append(f"  - {owner}: {count}")
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------------

async def _run(commit: bool) -> int:
    async with AsyncSessionLocal() as db:
        leads = await _fetch_candidates(db)
        plan = plan_backfill(leads)
        print(render_plan(plan))

        if not commit:
            print(f"\nDRY RUN -- {len(plan['leads'])} lead(s) would be moved to 'awaiting_deck'. "
                  "Re-run with --commit to apply.")
            return 0

        if not plan["leads"]:
            print("\nNothing to do.")
            return 0

        applied = await apply_backfill(db, leads)
        print(f"\nDone. {applied} lead(s) moved to 'awaiting_deck'.")
        return 0


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true",
                         help="Apply the writes. Without this flag, dry-run only (no writes).")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.commit))


if __name__ == "__main__":
    sys.exit(main())
