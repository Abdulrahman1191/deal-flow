"""
Copper <-> dashboard reconciliation report.

Explains the gap between Copper's "My Open Leads" count and the dashboard's
active-lead count by pulling both sets for one user, matching them, and
listing *every* lead the board is missing with a concrete reason -- so the
two totals reconcile down to the individual lead. Also reports drift the
other way (leads on the board that Copper no longer considers open).

Categories for "Copper-Open but NOT on the dashboard board":
  archived           -- app row is archived; sub_reason from the LeadEvent
                         EVENT_ARCHIVED/EVENT_ARCHIVED_NO_REPLY payload
                         (e.g. duplicate / rejection / no_reply / copper_reconcile
                         / deleted_in_copper -- whatever reason was logged).
  approved           -- in the send queue (status=approved).
  not_synced         -- copper_id present in Copper but no matching lead row.
  converted_or_sent  -- matched a lead whose copper_id was nulled (converted
                         to an Opportunity) -- the original Copper id survives
                         only in raw_copper_data, so this is the fallback match.
  status_mismatch    -- matched but the app row is in an unrecognized state.

Categories for "On the dashboard but NOT in Copper-Open":
  no_copper_id       -- app row has no copper_id at all.
  not_open_in_copper -- copper_id set, but Copper no longer reports it open
                         for this user (status changed externally, reassigned,
                         etc.) and the app hasn't archived it.

Strictly read-only: no writes to the DB or Copper, no dedup/sync side effects.

Usage (from backend/):
  python scripts/reconcile_copper.py
  python scripts/reconcile_copper.py --owner someone@raed.vc
  python scripts/reconcile_copper.py --copper-user-id 12345
  python scripts/reconcile_copper.py --markdown /tmp/reconcile.md

Reads DATABASE_URL + the Copper env (COPPER_API_KEY, COPPER_USER_EMAIL,
COPPER_OPEN_STATUS_ID, and COPPER_USER_ID unless --copper-user-id is given).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.event import LeadEvent
from app.models.lead import Lead
from app.services.copper_service import fetch_open_leads_for_user

DASHBOARD_EXCLUDED_STATUSES = ("archived", "approved")
KNOWN_ACTIVE_STATUSES = {"pending", "processing", "assessed"}
ARCHIVE_EVENT_TYPES = ("archived", "archived_no_reply")


# --- Data shape passed into the pure classifier helpers ----------------------

@dataclass
class AppLead:
    id: str
    company_name: str
    copper_id: Optional[str]
    # The Copper lead id captured in raw_copper_data at creation time. Only
    # populated when copper_id has since been nulled (conversion) -- it's the
    # sole surviving link back to the original Copper Lead in that case.
    raw_copper_id: Optional[str]
    status: str
    archive_event_type: Optional[str] = None
    archive_reason: Optional[str] = None


# --- Classifier helpers (pure, unit-tested, no DB/HTTP access) --------------

def classify_archive_reason(archive_event_type: Optional[str], archive_reason: Optional[str]) -> str:
    """Maps a LeadEvent archive record to a human sub-reason."""
    if archive_event_type == "archived_no_reply":
        return "no_reply"
    if not archive_reason:
        return "unspecified"
    reason = str(archive_reason)
    if reason.startswith("copper_reconcile"):
        return "copper_reconcile"
    return reason


def index_leads(app_leads: list) -> tuple[dict, dict]:
    """Splits app leads into a copper_id index and a raw-id fallback index
    (leads whose copper_id has been cleared, e.g. by conversion)."""
    by_copper_id: dict = {}
    by_raw_copper_id: dict = {}
    for lead in app_leads:
        if lead.copper_id:
            by_copper_id[lead.copper_id] = lead
        elif lead.raw_copper_id:
            by_raw_copper_id[lead.raw_copper_id] = lead
    return by_copper_id, by_raw_copper_id


def categorize_copper_open(copper_leads: list, by_copper_id: dict, by_raw_copper_id: dict) -> tuple[list, Counter]:
    """copper_leads: raw Copper lead dicts (the user's full open set).
    Returns (rows, counts). `rows` covers every Copper-open lead that is NOT
    on the dashboard board, one dict per lead with a reason (and sub_reason
    for archived). `counts` also includes an "on_dashboard" entry for leads
    that matched with an active status -- present in both sets, not a gap --
    so counts.values() sums to len(copper_leads)."""
    rows: list = []
    counts: Counter = Counter()

    for c in copper_leads:
        cid = str(c.get("id", ""))
        company = c.get("company_name") or c.get("name") or "Unknown"

        lead = by_copper_id.get(cid)
        if lead is not None:
            if lead.status in KNOWN_ACTIVE_STATUSES:
                counts["on_dashboard"] += 1
                continue
            category = lead.status if lead.status in ("approved", "archived") else "status_mismatch"
        elif cid in by_raw_copper_id:
            lead = by_raw_copper_id[cid]
            category = "converted_or_sent"
        else:
            lead = None
            category = "not_synced"

        row = {"company_name": company, "copper_id": cid, "reason": category}
        if category == "archived":
            row["sub_reason"] = classify_archive_reason(lead.archive_event_type, lead.archive_reason)
        counts[category] += 1
        rows.append(row)

    return rows, counts


def categorize_dashboard_only(dashboard_leads: list, copper_open_ids: set) -> tuple[list, Counter]:
    """dashboard_leads: app leads with status NOT IN (archived, approved) for
    the owner. Returns (rows, counts) for leads on the board but not in
    Copper's open set for this user -- drift the other direction."""
    rows: list = []
    counts: Counter = Counter()
    for lead in dashboard_leads:
        if lead.copper_id and lead.copper_id in copper_open_ids:
            continue
        reason = "no_copper_id" if not lead.copper_id else "not_open_in_copper"
        counts[reason] += 1
        rows.append({"company_name": lead.company_name, "copper_id": lead.copper_id, "reason": reason})
    return rows, counts


def build_reconciliation(copper_leads: list, app_leads: list) -> dict:
    """Pure reconciliation: no DB/HTTP access. copper_leads is the user's raw
    Copper open set; app_leads is every app Lead row for that owner (any
    status). Returns a dict whose counts reconcile: copper_open ==
    on_dashboard_and_copper_open + sum(missing_from_dashboard.counts), and
    dashboard_active == on_dashboard_and_copper_open + len(dashboard_not_in_copper.rows).
    """
    by_copper_id, by_raw_copper_id = index_leads(app_leads)
    missing_rows, missing_counts = categorize_copper_open(copper_leads, by_copper_id, by_raw_copper_id)
    on_both = missing_counts.pop("on_dashboard", 0)

    dashboard_leads = [l for l in app_leads if l.status not in DASHBOARD_EXCLUDED_STATUSES]
    copper_open_ids = {str(c.get("id", "")) for c in copper_leads}
    reverse_rows, reverse_counts = categorize_dashboard_only(dashboard_leads, copper_open_ids)

    return {
        "copper_open": len(copper_leads),
        "dashboard_active": len(dashboard_leads),
        "on_dashboard_and_copper_open": on_both,
        "missing_from_dashboard": {"rows": missing_rows, "counts": dict(missing_counts)},
        "dashboard_not_in_copper": {"rows": reverse_rows, "counts": dict(reverse_counts)},
    }


# --- DB access ----------------------------------------------------------------

async def fetch_app_leads(db, owner_email: str) -> list:
    result = await db.execute(
        select(Lead).options(joinedload(Lead.assessment)).where(Lead.owner_email == owner_email)
    )
    leads = result.unique().scalars().all()

    out: list = []
    for lead in leads:
        archive_event_type = None
        archive_reason = None
        if lead.status == "archived":
            ev = await db.execute(
                select(LeadEvent)
                .where(LeadEvent.lead_id == lead.id)
                .where(LeadEvent.event_type.in_(ARCHIVE_EVENT_TYPES))
                .order_by(LeadEvent.created_at.desc())
                .limit(1)
            )
            last = ev.scalar_one_or_none()
            if last:
                archive_event_type = last.event_type
                archive_reason = (last.payload or {}).get("reason") if last.payload else None

        raw_copper_id = None
        if not lead.copper_id and lead.raw_copper_data:
            raw_id = lead.raw_copper_data.get("id")
            raw_copper_id = str(raw_id) if raw_id else None

        out.append(AppLead(
            id=str(lead.id),
            company_name=lead.company_name,
            copper_id=lead.copper_id,
            raw_copper_id=raw_copper_id,
            status=lead.status,
            archive_event_type=archive_event_type,
            archive_reason=archive_reason,
        ))
    return out


async def _fetch_and_build(copper_user_id: Optional[int], owner_email: str) -> dict:
    copper_leads = fetch_open_leads_for_user(copper_user_id)
    async with AsyncSessionLocal() as db:
        app_leads = await fetch_app_leads(db, owner_email)
    return build_reconciliation(copper_leads, app_leads)


# --- Rendering ------------------------------------------------------------

def _row_line(row: dict) -> str:
    extra = f" ({row['sub_reason']})" if "sub_reason" in row else ""
    copper_id = row["copper_id"] or "(none)"
    return f"  - {row['company_name']}  [copper_id={copper_id}]  -> {row['reason']}{extra}"


def render_stdout(result: dict, owner_email: str) -> str:
    mf, dn = result["missing_from_dashboard"], result["dashboard_not_in_copper"]
    lines = [
        "Copper <-> dashboard reconciliation",
        f"Owner: {owner_email}",
        "",
        f"copper_open                 = {result['copper_open']}",
        f"dashboard_active            = {result['dashboard_active']}",
        f"on both (matched, no gap)   = {result['on_dashboard_and_copper_open']}",
        "",
        "=== In Copper-Open but NOT on the dashboard board ===",
        f"Total: {sum(mf['counts'].values())}",
    ]
    for cat, n in sorted(mf["counts"].items()):
        lines.append(f"  {cat}: {n}")
    lines.append("")
    lines.extend(_row_line(r) for r in mf["rows"])
    lines.append("")
    lines.append("=== On the dashboard but NOT in Copper-Open ===")
    lines.append(f"Total: {sum(dn['counts'].values())}")
    for cat, n in sorted(dn["counts"].items()):
        lines.append(f"  {cat}: {n}")
    lines.append("")
    lines.extend(_row_line(r) for r in dn["rows"])
    return "\n".join(lines)


def render_markdown(result: dict, owner_email: str) -> str:
    mf, dn = result["missing_from_dashboard"], result["dashboard_not_in_copper"]
    lines = [
        "# Copper <-> dashboard reconciliation",
        "",
        f"Owner: {owner_email}",
        "",
        f"- copper_open = {result['copper_open']}",
        f"- dashboard_active = {result['dashboard_active']}",
        f"- on both (matched, no gap) = {result['on_dashboard_and_copper_open']}",
        "",
        "## In Copper-Open but NOT on the dashboard board",
        "",
        f"Total: {sum(mf['counts'].values())}",
        "",
    ]
    lines.extend(f"- {cat}: {n}" for cat, n in sorted(mf["counts"].items()))
    lines.append("")
    lines.extend(_row_line(r).replace("  - ", "- ", 1) for r in mf["rows"])
    lines.append("")
    lines.append("## On the dashboard but NOT in Copper-Open")
    lines.append("")
    lines.append(f"Total: {sum(dn['counts'].values())}")
    lines.append("")
    lines.extend(f"- {cat}: {n}" for cat, n in sorted(dn["counts"].items()))
    lines.append("")
    lines.extend(_row_line(r).replace("  - ", "- ", 1) for r in dn["rows"])
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------

def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--copper-user-id", type=int, default=None,
                         help="Copper user id whose open leads to reconcile (default: settings.copper_user_id).")
    parser.add_argument("--owner", default=None,
                         help="App owner_email to reconcile against (default: settings.owner_email).")
    parser.add_argument("--markdown", help="Write the report as markdown to this path.")
    args = parser.parse_args(argv)

    owner_email = args.owner or settings.owner_email
    result = asyncio.run(_fetch_and_build(args.copper_user_id, owner_email))

    print(render_stdout(result, owner_email))

    if args.markdown:
        Path(args.markdown).write_text(render_markdown(result, owner_email))
        print(f"\nMarkdown report written to {args.markdown}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
