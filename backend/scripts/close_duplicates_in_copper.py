"""
One-time cleanup: closes the Copper record of every duplicate lead the
nightly dedup (app.services.dedup, run by app/tasks/dedupe_leads.py) already
archived locally.

Why. dedupe_leads() archives the loser of a same-owner/same-company pair
locally and logs an `archived` LeadEvent with {"reason": "duplicate",
"canonical": <surviving lead uuid>} -- but deliberately never writes back to
Copper. Copper therefore keeps both copies open forever, which is why
Copper's per-partner open-lead counts run higher than the board's, and why
partners have been merging pairs by hand in Copper (issue #178).

For every lead archived locally with an `archived` event carrying
{"reason": "duplicate"}, this script verifies liveness directly against
Copper (fetch_lead_by_id), per lead, immediately before acting -- never off
a stale bulk snapshot -- and then either:
  close        -- the duplicate's Copper record is still Open and the
                   surviving twin's Copper record is confirmed live -> enqueue
                   Unqualified + `raed:duplicate` tag + a Details custom field
                   naming the surviving record, through the durable
                   copper_outbox (copper_writer.close_duplicate_in_copper).
  skip         -- the duplicate's Copper record already 404s (deleted/merged
                   by hand), is already Unqualified, or is in some other
                   non-open status. No write.
  needs_review -- the surviving twin's OWN Copper record is missing. This is
                   the hidden-lead pattern (a lead open in Copper, archived on
                   the board, invisible to its partner) -- reported, and the
                   only live record is NEVER closed.

Prefers Lead.linked_copper_ids (issue #175, once landed) over the archived
event's "canonical" pointer to resolve the surviving twin, since that field
is kept current across re-merges while the event is a one-time snapshot.
Falls back to the event automatically today, since the column doesn't exist
yet.

Dry-run by default: prints the plan, makes NO writes, no DB changes. --commit
applies it. Per-lead try/except -- one failure/one Copper hiccup never aborts
the rest. --limit bounds a single run so it can be worked in batches.
Idempotent: a second run finds every previously-closed record already
Unqualified in Copper and skips it.

Usage (from backend/):
  python scripts/close_duplicates_in_copper.py                # dry run
  python scripts/close_duplicates_in_copper.py --commit
  python scripts/close_duplicates_in_copper.py --commit --limit 10

Reads DATABASE_URL + the Copper env (COPPER_API_KEY, COPPER_USER_EMAIL,
COPPER_OPEN_STATUS_ID, COPPER_UNQUALIFIED_STATUS_ID, and the Unqualified
Details custom field COPPER_CF_UNQUAL_DETAIL_ID).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.event import LeadEvent
from app.models.lead import Lead
from app.services import copper_writer
from app.services.copper_service import fetch_lead_by_id
from app.services.events import EVENT_ARCHIVED

DUPLICATE_REASON = "duplicate"


# --- Pure decision step (no DB/HTTP access) -----------------------------------

def decide_action(
    duplicate_copper: Optional[dict],
    surviving_copper: Optional[dict],
    surviving_company: Optional[str],
    surviving_copper_id: Optional[str],
) -> dict:
    """Given the current Copper state of a duplicate pair, decides what (if
    anything) to do about the duplicate's own Copper record.

    duplicate_copper: fetch_lead_by_id() result for the duplicate's Copper
        lead, or None if it 404s.
    surviving_copper: fetch_lead_by_id() result for the surviving twin's
        Copper lead, or None if there's no resolvable surviving lead at all,
        or its Copper record 404s.

    Returns {"action": "close" | "skip" | "needs_review", "reason": str,
    "detail_text": str} -- detail_text is only meaningful when action=="close".
    """
    if duplicate_copper is None:
        return {"action": "skip", "reason": "Copper record 404 (already deleted/merged)"}

    status_id = duplicate_copper.get("status_id")
    if status_id == settings.copper_unqualified_status_id:
        return {"action": "skip", "reason": "already Unqualified in Copper"}
    if settings.copper_open_status_id and status_id != settings.copper_open_status_id:
        return {"action": "skip", "reason": f"not in open status (status_id={status_id!r})"}

    if surviving_copper is None:
        return {
            "action": "needs_review",
            "reason": (
                f"surviving record Copper #{surviving_copper_id} is missing -- "
                "hidden-lead pattern, needs manual review, NOT closing the only live record"
            ),
        }

    return {
        "action": "close",
        "reason": "duplicate is Open in Copper and the surviving twin is confirmed live",
        "detail_text": f"Duplicate of {surviving_company} (Copper #{surviving_copper_id})",
    }


# --- DB access --------------------------------------------------------------

async def find_duplicate_leads(db, limit: Optional[int] = None) -> list:
    """Every locally-archived lead whose latest `archived` event carries
    {"reason": "duplicate"}. Returns a list of (lead, canonical_id) pairs,
    canonical_id being the uuid string from the event payload (may be None
    if the event predates that field). Only considers leads that still HAVE
    a copper_id -- nothing to close in Copper without one -- and that are
    STILL archived now (an undo since would have flipped status back)."""
    leads_result = await db.execute(
        select(Lead).where(Lead.status == "archived").where(Lead.copper_id.isnot(None))
    )
    archived_leads = {l.id: l for l in leads_result.scalars().all()}
    if not archived_leads:
        return []

    events_result = await db.execute(
        select(LeadEvent)
        .where(LeadEvent.event_type == EVENT_ARCHIVED)
        .where(LeadEvent.lead_id.in_(archived_leads.keys()))
        .order_by(LeadEvent.created_at.asc())
    )
    # ascending order -> the last write per lead_id wins, i.e. the latest event
    latest_event_by_lead: dict = {}
    for ev in events_result.scalars().all():
        latest_event_by_lead[ev.lead_id] = ev

    candidates = []
    for lead_id, lead in archived_leads.items():
        ev = latest_event_by_lead.get(lead_id)
        if not ev or (ev.payload or {}).get("reason") != DUPLICATE_REASON:
            continue
        candidates.append((lead, (ev.payload or {}).get("canonical")))

    candidates.sort(key=lambda pair: pair[0].company_name or "")
    if limit:
        candidates = candidates[:limit]
    return candidates


async def _resolve_surviving_lead(db, duplicate_lead, canonical_id: Optional[str]):
    """Prefers Lead.linked_copper_ids (issue #175) when it exists on the
    model, since it's kept current across re-merges; falls back to the
    archived event's "canonical" pointer -- a one-time snapshot that can go
    stale if the canonical lead itself later got merged into something else.
    The hasattr guard means this activates automatically once #175 lands,
    with no change needed here."""
    if hasattr(Lead, "linked_copper_ids") and duplicate_lead.copper_id:
        r = await db.execute(
            select(Lead).where(Lead.linked_copper_ids.contains([duplicate_lead.copper_id]))
        )
        surviving = r.scalars().first()
        if surviving is not None:
            return surviving

    if not canonical_id:
        return None
    try:
        canonical_uuid = uuid.UUID(str(canonical_id))
    except (ValueError, TypeError):
        return None
    return await db.get(Lead, canonical_uuid)


async def gather_rows(db, limit: Optional[int] = None) -> list:
    """Builds one plan row per duplicate candidate: resolves the surviving
    lead, fetches both Copper records fresh, and runs decide_action(). A
    per-candidate try/except means one DB/HTTP hiccup only skips that lead,
    never aborts the batch."""
    candidates = await find_duplicate_leads(db, limit=limit)
    rows = []
    for lead, canonical_id in candidates:
        row = {"lead_id": str(lead.id), "company_name": lead.company_name, "copper_id": lead.copper_id}
        try:
            surviving = await _resolve_surviving_lead(db, lead, canonical_id)
            if surviving is None:
                row["decision"] = {
                    "action": "needs_review",
                    "reason": "no resolvable surviving lead (canonical row missing or event lacks one)",
                }
                rows.append(row)
                continue

            duplicate_copper = fetch_lead_by_id(lead.copper_id)
            surviving_copper = fetch_lead_by_id(surviving.copper_id) if surviving.copper_id else None
            row["surviving_copper_id"] = surviving.copper_id
            row["existing_tags"] = (duplicate_copper or {}).get("tags")
            row["decision"] = decide_action(
                duplicate_copper, surviving_copper, surviving.company_name, surviving.copper_id,
            )
        except Exception as exc:
            row["decision"] = {"action": "skip", "reason": f"error building plan: {exc!r}"}
        rows.append(row)
    return rows


async def _fetch_plan(limit: Optional[int]) -> list:
    async with AsyncSessionLocal() as db:
        return await gather_rows(db, limit=limit)


# --- Writes -------------------------------------------------------------------

def apply_plan(to_close: list) -> dict:
    """Applies the plan's "close" rows against real Copper (via the outbox).
    Per-lead try/except so one failure never aborts the rest."""
    result = {"closed": [], "failed": []}
    for row in to_close:
        cid = row["copper_id"]
        try:
            outbox_id = copper_writer.close_duplicate_in_copper(
                cid, row.get("existing_tags"), row["decision"]["detail_text"],
            )
            result["closed"].append(cid)
            print(f"  OK    closed: {row['company_name']}  [copper_id={cid}]  outbox_id={outbox_id}")
        except Exception as exc:
            result["failed"].append(cid)
            print(f"  FAIL  closed: {row['company_name']}  [copper_id={cid}] -- {exc!r}")
    return result


# --- Rendering ------------------------------------------------------------

def render_plan(plan: list) -> str:
    to_close = [r for r in plan if r["decision"]["action"] == "close"]
    to_skip = [r for r in plan if r["decision"]["action"] == "skip"]
    to_review = [r for r in plan if r["decision"]["action"] == "needs_review"]

    lines = ["Close duplicate Copper records", ""]
    lines.append(f"=== To close (Unqualified + raed:duplicate): {len(to_close)} ===")
    for r in to_close:
        lines.append(f"  - {r['company_name']}  [copper_id={r['copper_id']}] -- {r['decision']['detail_text']}")
    lines.append("")
    lines.append(f"=== Skipped: {len(to_skip)} ===")
    for r in to_skip:
        lines.append(f"  - {r['company_name']}  [copper_id={r['copper_id']}] -- {r['decision']['reason']}")
    lines.append("")
    lines.append(f"=== Needs manual review (hidden-lead pattern): {len(to_review)} ===")
    for r in to_review:
        lines.append(f"  - {r['company_name']}  [copper_id={r['copper_id']}] -- {r['decision']['reason']}")
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------

def _check_env() -> None:
    """Refuses to run with a clear message when required Copper settings are
    unset, rather than failing confusingly deep inside an HTTP call."""
    missing = []
    if not settings.copper_api_key:
        missing.append("COPPER_API_KEY")
    if not settings.copper_user_email:
        missing.append("COPPER_USER_EMAIL")
    if not settings.copper_open_status_id:
        missing.append("COPPER_OPEN_STATUS_ID")
    if not settings.copper_unqualified_status_id:
        missing.append("COPPER_UNQUALIFIED_STATUS_ID")
    if missing:
        print(f"BLOCKED: missing required Copper setting(s): {', '.join(missing)}. "
              f"Set them in the environment before running.")
        sys.exit(2)


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true",
                         help="Apply the writes. Without this flag, dry-run only (no writes).")
    parser.add_argument("--limit", type=int, default=None,
                         help="Process at most N duplicate leads this run (for batching).")
    args = parser.parse_args(argv)

    _check_env()

    plan = asyncio.run(_fetch_plan(args.limit))
    print(render_plan(plan))

    to_close = [r for r in plan if r["decision"]["action"] == "close"]
    to_review = [r for r in plan if r["decision"]["action"] == "needs_review"]
    if to_review:
        print(f"\n{len(to_review)} lead(s) need manual review -- see above. Not touched.")

    if not args.commit:
        print(f"\nDRY RUN -- {len(to_close)} lead(s) would be closed. Re-run with --commit to apply.")
        return 0

    if not to_close:
        print("\nNothing to close.")
        return 0

    print(f"\nApplying to {len(to_close)} lead(s)...")
    result = apply_plan(to_close)
    failed = len(result["failed"])
    print(
        f"\nDone. {len(result['closed'])} closed, {failed} failed, "
        f"{len(plan) - len(to_close)} skipped/needing review."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
