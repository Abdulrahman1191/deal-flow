"""
Manual Copper reconcile — same semantics as the scheduled sync task, runnable
from a terminal (no Celery/Redis needed; assessment runs inline).

  1. IMPORT  — open Copper leads assigned to the user that we don't have yet
               are created and assessed inline (research + LLM, ~30-60s each).
  2. ARCHIVE — active local leads whose copper_id is no longer open-assigned
               are archived (logged as copper_reconcile, reversible).

Safety: refuses to reconcile if Copper returns 0 leads; never touches leads
without a copper_id; never un-archives.

Usage:
  DATABASE_URL=...neon...?ssl=require COPPER_API_KEY=... COPPER_USER_EMAIL=... \
  COPPER_USER_ID=... COPPER_OPEN_STATUS_ID=... DEEP_SEEK_API=... TAVILY_API_KEY=... \
    /tmp/eval-venv/bin/python scripts/run_copper_reconcile.py            # dry run
    ... scripts/run_copper_reconcile.py --commit                        # apply
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.models.assessment import AssessmentCard
from app.services.copper_service import fetch_open_leads_for_user, map_copper_lead

COMMIT = "--commit" in sys.argv


async def _assess_inline(db, lead) -> str:
    """Research + assess one imported lead inline (no Celery)."""
    from app.services import claude_agent, research, feedback_patterns

    lead_data = {
        "company_name": lead.company_name, "website": lead.website,
        "description": lead.description, "stage": lead.stage, "region": lead.region,
        "founder_names": lead.founder_names, "linkedin_urls": lead.linkedin_urls,
        "company_linkedin_url": lead.company_linkedin_url,
        "pitch_deck_text": lead.pitch_deck_text,
    }
    try:
        research_data = research.research_company(lead_data)
    except Exception as exc:
        print(f"     research failed ({exc!r}); assessing on lead data only")
        research_data = {}
    try:
        cal = await feedback_patterns.retrieve_labeled_exemplars(
            db, " ".join(filter(None, [lead.description or "", (lead.pitch_deck_text or "")[:3000]])),
            k=4, exclude_lead_id=lead.id)
    except Exception:
        cal = []
    result = claude_agent.assess_lead(lead_data, research_data, team_calibration=cal)
    db.add(AssessmentCard(
        lead_id=lead.id,
        bucket=result["bucket"], confidence_score=result["confidence_score"],
        summary=result.get("summary"), positive_signals=result.get("positive_signals"),
        red_flags=result.get("red_flags"), data_gaps=result.get("data_gaps"),
        scoring_breakdown=result.get("scoring_breakdown"),
        draft_subject=result.get("draft_subject"), draft_body=result.get("draft_body"),
        draft_type=result.get("draft_type"), research_sources=result.get("research_sources"),
        research_data=research_data, precedents_cited=result.get("precedents_cited"),
    ))
    lead.status = "assessed"
    return result["bucket"]


async def main():
    raw = fetch_open_leads_for_user()
    print(f"Copper: {len(raw)} open leads assigned to the user")
    if not raw:
        print("Copper returned 0 leads — refusing to reconcile (safety). Aborting.")
        return
    copper_ids = {str(r.get("id", "")) for r in raw if r.get("id")}

    async with AsyncSessionLocal() as db:
        existing = {
            l.copper_id for l in
            (await db.execute(select(Lead).where(Lead.copper_id.is_not(None)))).scalars().all()
        }
        to_import = [r for r in raw if str(r.get("id", "")) not in existing]
        active = (await db.execute(
            select(Lead).where(Lead.copper_id.is_not(None), Lead.status != "archived")
        )).scalars().all()
        stale = [l for l in active if l.copper_id not in copper_ids]

        print(f"Plan: import {len(to_import)} new lead(s), archive {len(stale)} stale lead(s)")
        for r in to_import:
            print(f"  + import: {map_copper_lead(r)['company_name']}")
        for l in stale[:10]:
            print(f"  - archive: {l.company_name}")
        if len(stale) > 10:
            print(f"  - ... and {len(stale)-10} more")

        if not COMMIT:
            print("\nDRY RUN — re-run with --commit to apply.")
            return

        # Archive stale
        for l in stale:
            l.status = "archived"
            try:
                from app.services.events import log_event, EVENT_ARCHIVED
                await log_event(db, l.id, EVENT_ARCHIVED,
                                {"reason": "copper_reconcile: no longer open-assigned to user"})
            except Exception:
                pass
        await db.commit()
        print(f"\nArchived {len(stale)} stale lead(s).")

        # Import + assess inline
        for i, r in enumerate(to_import, 1):
            lead = Lead(**map_copper_lead(r))
            db.add(lead)
            await db.flush()
            print(f"[{i}/{len(to_import)}] imported {lead.company_name} — assessing…")
            try:
                bucket = await _assess_inline(db, lead)
                print(f"     → {bucket}")
            except Exception as exc:
                lead.status = "pending"
                print(f"     assessment failed ({exc!r}); left as pending (re-assess in UI)")
            await db.commit()

        print("\nDone. Refresh the dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
