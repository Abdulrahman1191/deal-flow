"""
Bulk re-assess every lead with the current (pattern-based) model.

Mirrors app/tasks/assess_lead.py::assess_lead_task but with three deliberate
differences for a safe overnight batch:

  1. REUSES cached research_data from the latest card (the celery task always
     re-researches — ~6-7 fresh Tavily calls/lead, which would blow the dev-key
     quota across 391 leads). Fresh research is only attempted for leads with no
     cached research, and is best-effort (degrades to {} on any failure).
  2. Runs the feedback->pattern loop (retrieve_labeled_exemplars), so the team's
     thumbs/overrides calibrate the re-assessment.
  3. PRESERVES human verdicts — skips any lead with a user_override, and never
     resets user_override / user_rating / approved_at / sent_at.

Run (owner, against Neon):
  cd backend && DEEP_SEEK_API=... TAVILY_API_KEY=... DATABASE_URL=...neon...?ssl=require \
    caffeinate -i /tmp/eval-venv/bin/python scripts/reassess_all.py 2>&1 | tee /tmp/reassess.log

Idempotent + resumable: commits per lead, so a crash keeps completed work; a
re-run just rewrites the latest card for each lead.
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services import claude_agent, research, feedback_patterns


async def _latest_card(db, lead_id):
    r = await db.execute(
        select(AssessmentCard)
        .where(AssessmentCard.lead_id == lead_id)
        .order_by(AssessmentCard.created_at.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def main():
    started = time.time()
    buckets = Counter()
    skipped_override = skipped_archived = errors = 0

    async with AsyncSessionLocal() as db:
        leads = (await db.execute(select(Lead))).scalars().all()
        total = len(leads)
        print(f"Loaded {total} leads. Re-assessing (cached research + feedback loop)...\n", flush=True)

        for i, lead in enumerate(leads, 1):
            name = (lead.company_name or "?")[:32]
            try:
                if lead.status == "archived":
                    skipped_archived += 1
                    print(f"  {i:3d}/{total}  ARCHIVED-skip  {name}", flush=True)
                    continue

                card = await _latest_card(db, lead.id)

                if card and card.user_override:
                    skipped_override += 1
                    print(f"  {i:3d}/{total}  OVERRIDE-skip  {name} (human={card.user_override})", flush=True)
                    continue

                lead_data = {
                    "company_name": lead.company_name,
                    "website": lead.website,
                    "description": lead.description,
                    "stage": lead.stage,
                    "region": lead.region,
                    "founder_names": lead.founder_names,
                    "linkedin_urls": lead.linkedin_urls,
                    "company_linkedin_url": lead.company_linkedin_url,
                    "pitch_deck_text": lead.pitch_deck_text,
                }

                # 1. cached research, else best-effort fresh (never fatal)
                research_data = card.research_data if (card and card.research_data) else None
                fresh = False
                if research_data is None:
                    try:
                        research_data = research.research_company(lead_data)
                        fresh = True
                    except Exception as exc:
                        print(f"        research failed for {name}: {exc!r} (using empty)", flush=True)
                        research_data = {}

                # 2. feedback->pattern loop (exclude this lead's own verdict)
                match_text = " ".join(
                    filter(None, [lead.description or "", (lead.pitch_deck_text or "")[:3000]])
                )
                try:
                    team_calibration = await feedback_patterns.retrieve_labeled_exemplars(
                        db, match_text, k=4, exclude_lead_id=lead.id
                    )
                except Exception as exc:
                    print(f"        exemplar retrieval failed for {name}: {exc!r}", flush=True)
                    team_calibration = []

                result = claude_agent.assess_lead(
                    lead_data, research_data or {}, team_calibration=team_calibration
                )

                # 3. upsert latest card — PRESERVE human/workflow fields
                fields = dict(
                    bucket=result["bucket"],
                    confidence_score=result["confidence_score"],
                    summary=result.get("summary"),
                    positive_signals=result.get("positive_signals"),
                    red_flags=result.get("red_flags"),
                    data_gaps=result.get("data_gaps"),
                    scoring_breakdown=result.get("scoring_breakdown"),
                    draft_subject=result.get("draft_subject"),
                    draft_body=result.get("draft_body"),
                    draft_type=result.get("draft_type"),
                    research_sources=result.get("research_sources"),
                    research_data=research_data or {},
                    precedents_cited=result.get("precedents_cited"),
                )
                if card:
                    for k, v in fields.items():
                        setattr(card, k, v)
                else:
                    card = AssessmentCard(lead_id=lead.id, **fields)
                    db.add(card)

                if lead.status != "archived":
                    lead.status = "assessed"

                await db.commit()  # commit per lead → resumable

                b = (result.get("bucket") or "?").upper()
                buckets[b] += 1
                tag = "fresh" if fresh else "cached"
                print(
                    f"  {i:3d}/{total}  {b:6s} c={result.get('confidence_score')}"
                    f"  prec={len(team_calibration)}  [{tag}]  {name}",
                    flush=True,
                )
            except Exception as exc:
                errors += 1
                await db.rollback()
                print(f"  {i:3d}/{total}  ERROR  {name}: {exc!r}", flush=True)
                traceback.print_exc()

    dt = round(time.time() - started, 1)
    print(f"\n=== DONE in {dt}s ===", flush=True)
    print(f"  buckets: {dict(buckets)}", flush=True)
    print(f"  skipped (human override): {skipped_override}", flush=True)
    print(f"  skipped (archived): {skipped_archived}", flush=True)
    print(f"  errors: {errors}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
