"""
Ownership-reconciliation report: Dealflow `owner_email` vs. current Copper
`assignee_id`.

Some leads show under the wrong partner in Dealflow because a lead was
assigned in Copper to person A, then reassigned to person B, but Dealflow
still has owner_email = A (ownership drift left behind by the reassignment
history -- sync_copper.py only reconciles on its periodic pass, and only
catches drift for users it actively syncs). Copper is the source of truth:
this report walks every lead with a copper_id, resolves that lead's *current*
Copper assignee to a @raed.vc email via users.copper_user_id, and lists every
lead where that differs from Dealflow's owner_email -- both directions.

Report-only by default. Pass --fix to correct owner_email to match Copper
for every mismatch whose assignee resolves to a known user (mismatches whose
Copper assignee is unassigned or isn't in the users table are always left
alone -- there's nothing known to fix them to). Never writes to Copper.

Usage (from backend/):
  python scripts/reconcile_ownership.py
  python scripts/reconcile_ownership.py --owner abdulrahman@raed.vc
  python scripts/reconcile_ownership.py --pair abdulrahman@raed.vc,uday@raed.vc
  python scripts/reconcile_ownership.py --markdown /tmp/ownership.md
  python scripts/reconcile_ownership.py --fix

Reads DATABASE_URL + the Copper env (COPPER_API_KEY, COPPER_USER_EMAIL).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.models.user import User
from app.services.copper_service import fetch_all_leads

UNASSIGNED = "unassigned"


# --- Data shapes passed into the pure classifier helpers ---------------------

@dataclass
class AppLead:
    id: str
    company_name: str
    copper_id: str
    owner_email: Optional[str]


@dataclass
class Mismatch:
    lead_id: str
    company_name: str
    copper_id: str
    dealflow_owner: str
    copper_assignee: str

    @property
    def fixable(self) -> bool:
        """True if copper_assignee resolved to a known @raed.vc user -- i.e.
        there's a concrete value --fix could write. unassigned/unknown(<id>)
        leads are always skipped by --fix; there's nothing known to set."""
        return self.copper_assignee not in (UNASSIGNED,) and not self.copper_assignee.startswith("unknown(")


# --- Classifier helpers (pure, unit-tested, no DB/HTTP access) --------------

def resolve_assignee_email(assignee_id, copper_user_map: dict) -> str:
    """Maps a Copper lead's assignee_id to a @raed.vc email via
    copper_user_id -> email. Falls back to a clear unassigned/unknown(<id>)
    label rather than silently dropping the lead from the report."""
    if not assignee_id:
        return UNASSIGNED
    try:
        aid = int(assignee_id)
    except (TypeError, ValueError):
        return f"unknown({assignee_id})"
    email = copper_user_map.get(aid)
    return email if email else f"unknown({aid})"


def find_mismatches(app_leads: list, copper_index: dict, copper_user_map: dict) -> list:
    """app_leads: AppLead rows with a copper_id. copper_index: copper_id (str)
    -> raw Copper lead dict, from a firm-wide fetch. copper_user_map:
    copper_user_id (int) -> @raed.vc email, from the users table.

    Returns one Mismatch per lead whose resolved current Copper assignee
    differs from Lead.owner_email. Leads whose copper_id isn't present in
    copper_index (e.g. deleted in Copper) are skipped -- there's no current
    assignee to compare against.
    """
    rows: list = []
    for lead in app_leads:
        raw = copper_index.get(lead.copper_id)
        if raw is None:
            continue
        copper_owner = resolve_assignee_email(raw.get("assignee_id"), copper_user_map)
        dealflow_owner = lead.owner_email or UNASSIGNED
        if dealflow_owner.strip().lower() == copper_owner.strip().lower():
            continue
        rows.append(Mismatch(
            lead_id=lead.id,
            company_name=lead.company_name,
            copper_id=lead.copper_id,
            dealflow_owner=dealflow_owner,
            copper_assignee=copper_owner,
        ))
    return rows


def filter_by_owner(rows: list, owner_email: str) -> list:
    """Scopes mismatches to ones involving `owner_email` on either side --
    covers both a lead drifting away from them and one that should be theirs."""
    target = owner_email.strip().lower()
    return [
        r for r in rows
        if r.dealflow_owner.strip().lower() == target or r.copper_assignee.strip().lower() == target
    ]


def filter_by_pair(rows: list, pair: tuple) -> list:
    """Scopes mismatches to ones crossing between exactly these two emails,
    in either direction -- the abdulrahman <-> uday convenience case."""
    a, b = (p.strip().lower() for p in pair)
    wanted = {a, b}
    return [
        r for r in rows
        if r.dealflow_owner.strip().lower() in wanted and r.copper_assignee.strip().lower() in wanted
    ]


def group_counts(rows: list) -> Counter:
    """Per-pair counts: (dealflow_owner, copper_assignee) -> count, so the
    report shows at a glance which reassignments account for the drift."""
    return Counter((r.dealflow_owner, r.copper_assignee) for r in rows)


def sort_rows(rows: list) -> list:
    return sorted(rows, key=lambda r: (r.dealflow_owner, r.copper_assignee, r.company_name))


# --- DB access ----------------------------------------------------------------

async def fetch_app_leads(db) -> list:
    """Firm-wide: every lead with a copper_id, any owner. --owner/--pair are
    applied afterwards via filter_by_owner/filter_by_pair so they can match on
    either side of a mismatch (a query filtered to Lead.owner_email == X would
    miss drift *into* X from another owner)."""
    result = await db.execute(select(Lead).where(Lead.copper_id.is_not(None)))
    leads = result.scalars().all()
    return [
        AppLead(id=str(l.id), company_name=l.company_name, copper_id=l.copper_id, owner_email=l.owner_email)
        for l in leads
    ]


async def fetch_copper_user_map(db) -> dict:
    result = await db.execute(select(User.copper_user_id, User.email).where(User.copper_user_id.is_not(None)))
    return {cid: email for cid, email in result.all()}


async def apply_fix(db, mismatches: list) -> list:
    """Sets owner_email = copper_assignee for every fixable mismatch. Skips
    unfixable ones (unassigned/unknown Copper assignee). Idempotent: rerunning
    against already-fixed leads finds no mismatches at all, since
    find_mismatches only reports leads where the two values still differ."""
    fixed: list = []
    lead_ids = [m.lead_id for m in mismatches if m.fixable]
    if not lead_ids:
        return fixed

    result = await db.execute(select(Lead).where(Lead.id.in_(uuid.UUID(i) for i in lead_ids)))
    leads_by_id = {str(l.id): l for l in result.scalars().all()}

    for m in mismatches:
        if not m.fixable:
            continue
        lead = leads_by_id.get(m.lead_id)
        if lead is None:
            continue
        lead.owner_email = m.copper_assignee
        fixed.append(m)

    if fixed:
        await db.commit()
    return fixed


async def _fetch_mismatches() -> list:
    copper_leads = fetch_all_leads()
    copper_index = {str(c.get("id", "")): c for c in copper_leads}
    async with AsyncSessionLocal() as db:
        app_leads = await fetch_app_leads(db)
        copper_user_map = await fetch_copper_user_map(db)
    return find_mismatches(app_leads, copper_index, copper_user_map)


async def _run_fix(rows: list) -> list:
    async with AsyncSessionLocal() as db:
        return await apply_fix(db, rows)


# --- Rendering ------------------------------------------------------------

def render_stdout(rows: list) -> str:
    rows = sort_rows(rows)
    counts = group_counts(rows)
    lines = [
        "Ownership reconciliation: Dealflow owner_email vs. current Copper assignee",
        f"Total mismatches: {len(rows)}",
        "",
        "Per-pair counts (dealflow_owner -> copper_assignee):",
    ]
    for (dealflow_owner, copper_owner), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {dealflow_owner} -> {copper_owner}: {n}")
    lines.append("")
    lines.append(f"{'company_name':<40} | {'dealflow_owner':<28} | {'copper_assignee':<28} | copper_id")
    lines.append("-" * 120)
    for r in rows:
        lines.append(f"{r.company_name:<40} | {r.dealflow_owner:<28} | {r.copper_assignee:<28} | {r.copper_id}")
    return "\n".join(lines)


def render_markdown(rows: list) -> str:
    rows = sort_rows(rows)
    counts = group_counts(rows)
    lines = [
        "# Ownership reconciliation: Dealflow owner_email vs. current Copper assignee",
        "",
        f"Total mismatches: {len(rows)}",
        "",
        "## Per-pair counts",
        "",
    ]
    for (dealflow_owner, copper_owner), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {dealflow_owner} -> {copper_owner}: {n}")
    lines.append("")
    lines.append("| company_name | dealflow_owner | copper_assignee | copper_id |")
    lines.append("| --- | --- | --- | --- |")
    for r in rows:
        lines.append(f"| {r.company_name} | {r.dealflow_owner} | {r.copper_assignee} | {r.copper_id} |")
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------

def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default=None,
                         help="Scope to mismatches involving this @raed.vc email on either side (default: firm-wide).")
    parser.add_argument("--pair", default=None,
                         help="Scope to mismatches crossing between two emails, e.g. "
                              "--pair abdulrahman@raed.vc,uday@raed.vc (either direction).")
    parser.add_argument("--markdown", help="Write the report as markdown to this path.")
    parser.add_argument("--fix", action="store_true",
                         help="Correct owner_email to match Copper for every fixable mismatch (default: report-only).")
    args = parser.parse_args(argv)

    pair = None
    if args.pair:
        parts = [p.strip() for p in args.pair.split(",")]
        if len(parts) != 2 or not all(parts):
            parser.error("--pair requires exactly two comma-separated emails, e.g. a@raed.vc,b@raed.vc")
        pair = tuple(parts)

    mismatches = asyncio.run(_fetch_mismatches())

    rows = mismatches
    if args.owner:
        rows = filter_by_owner(rows, args.owner)
    if pair:
        rows = filter_by_pair(rows, pair)

    print(render_stdout(rows))

    if args.markdown:
        Path(args.markdown).write_text(render_markdown(rows))
        print(f"\nMarkdown report written to {args.markdown}")

    if args.fix:
        fixed = asyncio.run(_run_fix(rows))
        print(f"\n--fix applied: corrected {len(fixed)} lead(s).")
        for m in fixed:
            print(f"  {m.company_name} [copper_id={m.copper_id}]  {m.dealflow_owner} -> {m.copper_assignee}")
        skipped = [m for m in rows if not m.fixable]
        if skipped:
            print(f"--fix skipped {len(skipped)} lead(s) with an unresolvable Copper assignee.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
