"""
De-duplicate leads by normalized company name.

The pipeline accumulates duplicates when the same company is entered into Copper
more than once (each gets its own copper_id, so our copper_id-based sync imports
each as a separate lead). This collapses each duplicate-name group to a single
canonical lead and ARCHIVES the rest.

Why archive, not delete:
  - The archived rows keep their copper_id, so a future Copper sync (which dedups
    by copper_id) won't re-import them. Hard-deleting would let them come back.
  - The board already hides status='archived', so they drop off the kanban.
  - It's reversible — flip status back if a "duplicate" was actually distinct.

Canonical selection (keep the richest / human-touched lead): prefers, in order,
a lead whose latest card has a human verdict (user_override/user_rating), then
any assessed lead, then one with a pitch deck, then research, then a copper_id,
then the oldest (original) record.

Usage (run against the DB; writes only with --commit):
  DATABASE_URL=...neon...?ssl=require /tmp/eval-venv/bin/python scripts/dedupe_leads.py            # dry run
  DATABASE_URL=...neon...?ssl=require /tmp/eval-venv/bin/python scripts/dedupe_leads.py --commit   # apply

Idempotent: only groups *active* (non-archived) leads, so a second run is a no-op.
Re-run it any time (e.g. after a Copper sync) to keep the board free of dupes.
"""
from __future__ import annotations

import asyncio
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead

COMMIT = "--commit" in sys.argv


def _norm(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


async def _latest_card(db, lead_id):
    r = await db.execute(
        select(AssessmentCard).where(AssessmentCard.lead_id == lead_id)
        .order_by(AssessmentCard.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


def _score(lead, card) -> tuple:
    human = 1 if (card and (card.user_override or card.user_rating)) else 0
    assessed = 1 if card else 0
    has_deck = 1 if len(lead.pitch_deck_text or "") > 50 else 0
    has_research = 1 if (card and card.research_data) else 0
    has_copper = 1 if lead.copper_id else 0
    # oldest first as final tiebreak (keep the original record)
    age = -(lead.created_at.timestamp() if lead.created_at else 0)
    return (human, assessed, has_deck, has_research, has_copper, age)


async def main():
    async with AsyncSessionLocal() as db:
        leads = (
            await db.execute(select(Lead).where(Lead.status != "archived"))
        ).scalars().all()

        groups: dict[str, list] = defaultdict(list)
        for l in leads:
            k = _norm(l.company_name)
            if k:
                groups[k].append(l)
        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        to_archive = sum(len(v) - 1 for v in dup_groups.values())

        print(f"== Lead de-dup ({'COMMIT' if COMMIT else 'DRY RUN'}) ==")
        print(f"{len(leads)} active leads | {len(dup_groups)} duplicate-name groups "
              f"| {to_archive} leads to archive\n")

        archived = 0
        for k, group in sorted(dup_groups.items()):
            scored = []
            for l in group:
                card = await _latest_card(db, l.id)
                scored.append((_score(l, card), l))
            scored.sort(key=lambda x: x[0], reverse=True)
            canonical = scored[0][1]
            losers = [l for _, l in scored[1:]]

            print(f"[{k}]  keep {str(canonical.id)[:8]} (copper={canonical.copper_id}) "
                  f"— archiving {len(losers)}:")
            for l in losers:
                print(f"     archive {str(l.id)[:8]} (copper={l.copper_id})")
                if COMMIT:
                    l.status = "archived"
                    try:
                        from app.services.events import log_event, EVENT_ARCHIVED
                        await log_event(db, l.id, EVENT_ARCHIVED,
                                        {"reason": "duplicate", "canonical": str(canonical.id)})
                    except Exception as exc:
                        print(f"       (event log skipped: {exc!r})")
                    archived += 1
            if COMMIT:
                await db.commit()

        print(f"\n{'COMMITTED — archived ' + str(archived) + ' duplicate(s).' if COMMIT else 'DRY RUN — re-run with --commit to apply.'}")


if __name__ == "__main__":
    asyncio.run(main())
