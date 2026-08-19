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

For the automatic, always-on equivalent of this script (issue #123), see the
`reconcile_ownership_task` beat task in app/tasks/reconcile_ownership.py --
both share their comparison/fix logic via app/services/ownership.py.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import AsyncSessionLocal
from app.services.copper_service import fetch_all_leads
from app.services.ownership import (  # noqa: F401 -- re-exported for callers/tests (`ro.AppLead`, etc.)
    UNASSIGNED,
    AppLead,
    Mismatch,
    apply_fix,
    fetch_app_leads,
    fetch_copper_user_map,
    filter_by_owner,
    filter_by_pair,
    find_mismatches,
    group_counts,
    resolve_assignee_email,
    sort_rows,
)


# --- DB access ----------------------------------------------------------------

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
