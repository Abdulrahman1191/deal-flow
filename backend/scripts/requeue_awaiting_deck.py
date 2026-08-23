"""
One-off backfill (issue #144): re-queue every lead currently parked in
`awaiting_deck` now that a deckless lead with a usable website or a
substantial description gets scored instead of parked (see
app/tasks/assess_lead.py::_run, the new gate). A lead that still has
genuinely no usable context -- no website content, thin/empty description --
lands right back in `awaiting_deck`, a harmless no-op.

This intentionally does NOT duplicate the live gate's scrape-and-decide logic:
assess_lead_task re-checks everything (including a fresh website scrape) at
run time, so it's the single source of truth. This script only re-enqueues
the task; the preview below (based on `description` length and whether a
`website` is present) is an unverified estimate to size the batch before
committing -- it does not perform any network scrape itself, so it can't miss
or over-count due to a slow/dead site.

Dry-run by default: prints the plan. --commit re-enqueues assess_lead_task
for every awaiting_deck lead (owner-agnostic, firm-wide).

Usage (from backend/):
  python scripts/requeue_awaiting_deck.py            # dry run
  python scripts/requeue_awaiting_deck.py --commit

Reads DATABASE_URL (and the Celery broker config via app.config) from the
environment.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.tasks.assess_lead import MIN_DESCRIPTION_CHARS, assess_lead_task


async def _fetch_candidates(db) -> list[Lead]:
    result = await db.execute(select(Lead).where(Lead.status == "awaiting_deck"))
    return list(result.scalars().all())


def _preview_bucket(lead: Lead) -> str:
    """Unverified estimate only -- the live gate in assess_lead._run is what
    actually decides, including a real website scrape."""
    description = (lead.description or "").strip()
    if len(description) >= MIN_DESCRIPTION_CHARS:
        return "substantial description"
    if lead.website:
        return "has website (unverified -- scraped live when re-queued)"
    return "likely still no usable context"


def render_plan(leads: list[Lead]) -> str:
    counts = Counter(_preview_bucket(lead) for lead in leads)
    lines = [
        "Re-queue awaiting_deck leads under the website/description fallback (issue #144)",
        f"=== Candidates: {len(leads)} ===",
    ]
    for bucket, count in sorted(counts.items()):
        lines.append(f"  - {bucket}: {count}")
    return "\n".join(lines)


async def _run(commit: bool) -> int:
    async with AsyncSessionLocal() as db:
        leads = await _fetch_candidates(db)
        print(render_plan(leads))

        if not commit:
            print(f"\nDRY RUN -- {len(leads)} lead(s) would be re-queued. "
                  "Re-run with --commit to apply.")
            return 0

        if not leads:
            print("\nNothing to do.")
            return 0

        for lead in leads:
            assess_lead_task.delay(str(lead.id))
        print(f"\nDone. Re-queued {len(leads)} lead(s) for re-assessment.")
        return 0


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true",
                         help="Actually enqueue assess_lead_task for every awaiting_deck lead.")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.commit))


if __name__ == "__main__":
    sys.exit(main())
