"""
Backfill empty draft_body for assessment cards.

Regenerates drafts via DeepSeek for any active lead whose effective bucket is
YES or REJECT but draft_body is null (typically caused by past LLM/template
bugs). Idempotent — skips cards that already have a draft body.

Usage: python scripts/backfill_drafts.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services import claude_agent


async def run() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AssessmentCard, Lead)
            .join(Lead, AssessmentCard.lead_id == Lead.id)
            .where(AssessmentCard.draft_body.is_(None))
            .where(Lead.status != "archived")
        )
        rows = result.all()

        targets = []
        for card, lead in rows:
            effective = card.user_override or card.bucket
            if effective in ("YES", "REJECT"):
                targets.append((card, lead, effective))

        print(f"Backfilling {len(targets)} draft(s)…")
        ok, fail = 0, 0
        for card, lead, bucket in targets:
            try:
                new_draft = claude_agent.regenerate_draft(
                    {"company_name": lead.company_name, "founder_names": lead.founder_names},
                    bucket,
                    card.summary or "",
                )
                card.draft_type = new_draft.get("draft_type")
                card.draft_subject = new_draft.get("draft_subject")
                card.draft_body = new_draft.get("draft_body")
                await db.commit()
                print(f"  OK   {lead.company_name} -> {bucket} / {card.draft_type}")
                ok += 1
            except Exception as exc:
                print(f"  FAIL {lead.company_name}: {exc!r}")
                fail += 1

        print(f"\nDone. {ok} succeeded, {fail} failed.")


if __name__ == "__main__":
    asyncio.run(run())
