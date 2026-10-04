"""
One-time (re-runnable) backfill: regenerates draft emails that were written
in the wrong language before issue #168's detection fix shipped (issue #177).

For every assessed lead whose latest card has a draft, compares
`claude_agent.detect_applicant_language` (the applicant's detected language,
from company_name / pitch_deck_text / Copper "Source detail" / description --
the same inputs the assessor and draft-regen paths use) against
`claude_agent.detect_draft_script` (the script the current draft_body is
actually written in). When they disagree, the draft is regenerated through
the same `_regenerate_draft_for_bucket` path the /override and
/regenerate-draft endpoints use, for the lead's EFFECTIVE bucket -- the
bucket itself is never touched, only the draft's language.

Never touches a committed draft: any card with `sent_at` or `approved_at`
set is skipped outright, as is any archived lead. A card with no draft at
all (a MAYBE, or a backfill gap) is also skipped -- this script only
rewrites an existing draft, it never manufactures one (see
backfill_drafts.py for that).

Regenerated drafts fall back to the default Raed Ventures Calendly link
(claude_agent.DEFAULT_CALENDLY_URL) rather than looking up each lead's
owner -- a one-off language fix doesn't need per-owner personalization, and
skipping that lookup keeps this script to a single DB round-trip.

Dry-run by default: prints the plan, makes NO writes and NO LLM calls (script
detection is pure/deterministic). --commit regenerates the mismatched drafts.
Per-lead try/except so one LLM failure doesn't abort the batch -- a failed
lead keeps its old (mismatched) draft, which the next run will retry.

Idempotent: once a draft is regenerated to match the detected language, a
second run finds it already matching and reports "no change" for it -- a
no-op is a success here, not a failure.

Usage (from backend/):
  python scripts/regenerate_mismatched_drafts.py                       # dry run, all owners
  python scripts/regenerate_mismatched_drafts.py --commit
  python scripts/regenerate_mismatched_drafts.py --owner waleed@raed.vc --commit
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.routers.assessments import _regenerate_draft_for_bucket
from app.services import language_audit


# --- Pure planning step (no DB/HTTP/LLM access) -------------------------------

def plan_regeneration(rows: list) -> list[dict]:
    """rows: (AssessmentCard, Lead) tuples, any order, duplicates per lead
    allowed (deduped here to each lead's latest card by created_at).

    Excludes outright (never listed, never regenerated):
      - archived leads
      - cards with sent_at or approved_at set (a committed draft)
      - cards with no draft_body at all (nothing to compare/rewrite)

    Every remaining row is in the plan, each marked "mismatched" or not --
    a "no change" entry is a normal, successful outcome.
    """
    latest_by_lead: dict = {}
    for card, lead in rows:
        existing = latest_by_lead.get(lead.id)
        if existing is None or card.created_at > existing[0].created_at:
            latest_by_lead[lead.id] = (card, lead)

    plan = []
    for card, lead in latest_by_lead.values():
        if lead.status == "archived":
            continue
        if card.sent_at or card.approved_at:
            continue
        if not card.draft_body:
            continue
        plan.append({
            "lead_id": str(lead.id),
            "company_name": lead.company_name,
            "card": card,
            "lead": lead,
            "effective_bucket": card.user_override or card.bucket,
            "mismatched": language_audit.language_mismatch(lead, card.draft_body),
        })
    return plan


# --- Writes (still pure w.r.t. DB -- caller commits) --------------------------

def apply_regeneration(plan: list[dict], regenerate_fn: Callable) -> dict:
    """Mutates `card.draft_type/draft_subject/draft_body/draft_bucket` in
    place for every mismatched entry, via
    `regenerate_fn(lead, bucket, summary) -> draft dict`. Per-lead
    try/except: a failure leaves that entry's card untouched (still
    mismatched, so the next run retries it) and is recorded in "failed"
    rather than aborting the rest. Caller is responsible for committing."""
    result: dict = {"regenerated": [], "no_change": [], "failed": []}
    for entry in plan:
        if not entry["mismatched"]:
            result["no_change"].append(entry["lead_id"])
            continue
        card, lead = entry["card"], entry["lead"]
        try:
            new_draft = regenerate_fn(lead, entry["effective_bucket"], card.summary or "")
            card.draft_type = new_draft.get("draft_type")
            card.draft_subject = new_draft.get("draft_subject")
            card.draft_body = new_draft.get("draft_body")
            card.draft_bucket = entry["effective_bucket"]
            result["regenerated"].append(entry["lead_id"])
        except Exception as exc:
            result["failed"].append(entry["lead_id"])
            print(f"  FAIL  {lead.company_name}  [lead_id={entry['lead_id']}] -- {exc!r}")
    return result


def _default_regenerate_fn(lead, bucket: str, summary: str) -> dict:
    return _regenerate_draft_for_bucket(lead, bucket, summary, {})


# --- DB access -----------------------------------------------------------------

async def _fetch_rows(db, owner_email: Optional[str]) -> list:
    query = select(AssessmentCard, Lead).join(Lead, AssessmentCard.lead_id == Lead.id)
    if owner_email:
        query = query.where(Lead.owner_email == owner_email)
    result = await db.execute(query.order_by(AssessmentCard.created_at.desc()))
    return result.all()


# --- Rendering ------------------------------------------------------------

def render_plan(plan: list[dict], owner_email: Optional[str]) -> str:
    mismatched = [e for e in plan if e["mismatched"]]
    no_change = [e for e in plan if not e["mismatched"]]
    lines = [
        "Regenerate mismatched-language drafts",
        f"Owner: {owner_email or '(all owners)'}",
        "",
        f"=== Mismatched (applicant language != draft script) -> regenerate: {len(mismatched)} ===",
    ]
    for e in mismatched:
        lines.append(f"  - {e['company_name']}  [lead_id={e['lead_id']}, bucket={e['effective_bucket']}]")
    lines.append("")
    lines.append(f"=== Already matching -> no change: {len(no_change)} ===")
    return "\n".join(lines)


# --- Orchestration --------------------------------------------------------

async def run(owner_email: Optional[str], commit: bool) -> dict:
    async with AsyncSessionLocal() as db:
        rows = await _fetch_rows(db, owner_email)
        plan = plan_regeneration(rows)
        print(render_plan(plan, owner_email))

        no_change_ids = [e["lead_id"] for e in plan if not e["mismatched"]]
        mismatched = [e for e in plan if e["mismatched"]]

        if not commit:
            print(f"\nDRY RUN -- {len(mismatched)} draft(s) would be regenerated. Re-run with --commit to apply.")
            return {"regenerated": [], "no_change": no_change_ids, "failed": []}

        if not mismatched:
            print("\nNothing to regenerate.")
            return {"regenerated": [], "no_change": no_change_ids, "failed": []}

        print(f"\nRegenerating {len(mismatched)} draft(s)...")
        outcome = apply_regeneration(plan, _default_regenerate_fn)
        await db.commit()
        for lead_id in outcome["regenerated"]:
            entry = next(e for e in plan if e["lead_id"] == lead_id)
            print(f"  OK    {entry['company_name']}  [lead_id={lead_id}] -> regenerated ({entry['effective_bucket']})")

        print(
            f"\nDone. {len(outcome['regenerated'])} regenerated, "
            f"{len(outcome['no_change'])} unchanged, {len(outcome['failed'])} failed."
        )
        return outcome


# --- CLI -------------------------------------------------------------------

def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default=None,
                         help="Scope to a single Lead.owner_email (default: every owner).")
    parser.add_argument("--commit", action="store_true",
                         help="Apply the regenerations. Without this flag, dry-run only (no writes, no LLM calls).")
    args = parser.parse_args(argv)

    outcome = asyncio.run(run(args.owner, args.commit))
    return 1 if outcome.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
