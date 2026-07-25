"""
One-time cleanup: closes Copper leads that are still "Open" in Copper but
already archived in the app (the drift reconcile_copper.py surfaces), and
removes leftover ZZZ-RAED-* test records.

Two independent write actions, planned by plan_cleanup():
  stale        -- Copper-open leads whose app row is archived -> set Copper
                  status_id to Unqualified so Copper matches the app.
  test_records -- Copper leads whose name/company starts with "ZZZ-RAED-" ->
                  deleted outright (or set Unqualified if delete fails).
A lead matching both only appears in test_records -- deletion is the
stronger action, so it never also gets an Unqualified write in the same run.

Dry-run by default: prints the plan, makes NO writes. --commit applies it.
Per-lead success/failure is logged; one failure does not abort the rest.

Writes go straight to Copper via copper_writer.execute_copper_request (NOT
the async outbox) -- this is a one-shot script, not a long-running worker.
Idempotent: fetch_open_leads_for_user only returns leads currently in the
Open status, so once a lead's status/existence changes, a second run no
longer sees it.

This only touches Copper. It never writes to the app DB or un-archives
anything -- the app-side fix that prevents new drift is a separate issue.

Usage (from backend/):
  python scripts/close_stale_copper.py                       # dry run
  python scripts/close_stale_copper.py --commit
  python scripts/close_stale_copper.py --owner someone@raed.vc --commit
  python scripts/close_stale_copper.py --copper-user-id 12345 --commit

Reads DATABASE_URL + the Copper env (COPPER_API_KEY, COPPER_USER_EMAIL,
COPPER_OPEN_STATUS_ID, COPPER_UNQUALIFIED_STATUS_ID, and COPPER_USER_ID
unless --copper-user-id is given).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.copper_service import fetch_open_leads_for_user
from app.services.copper_writer import execute_copper_request

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import reconcile_copper as rc  # noqa: E402 -- matching logic from issue #54

TEST_RECORD_PREFIX = "ZZZ-RAED-"


# --- Pure planning step (no DB/HTTP access) -----------------------------------

def _is_test_record(copper_lead: dict) -> bool:
    company = str(copper_lead.get("company_name") or "")
    name = str(copper_lead.get("name") or "")
    return company.startswith(TEST_RECORD_PREFIX) or name.startswith(TEST_RECORD_PREFIX)


def plan_cleanup(copper_leads: list, app_leads: list) -> dict:
    """copper_leads: the user's raw Copper-open set. app_leads: every app Lead
    row for that owner (any status), as reconcile_copper.AppLead instances.
    Returns {"stale": [...], "test_records": [...]}, each a list of
    {company_name, copper_id, ...}. A lead in test_records is never also in
    stale (see module docstring)."""
    by_copper_id, by_raw_copper_id = rc.index_leads(app_leads)
    rows, _counts = rc.categorize_copper_open(copper_leads, by_copper_id, by_raw_copper_id)
    archived_by_id = {r["copper_id"]: r for r in rows if r["reason"] == "archived"}

    test_records: list = []
    stale: list = []
    for c in copper_leads:
        cid = str(c.get("id", ""))
        if _is_test_record(c):
            company = c.get("company_name") or c.get("name") or "Unknown"
            test_records.append({"company_name": company, "copper_id": cid})
        elif cid in archived_by_id:
            stale.append(archived_by_id[cid])

    return {"stale": stale, "test_records": test_records}


# --- Writes ---------------------------------------------------------------

def _set_unqualified(copper_id: str) -> None:
    execute_copper_request(f"/leads/{copper_id}", "PUT", {"status_id": settings.copper_unqualified_status_id})


def _delete_lead(copper_id: str) -> None:
    execute_copper_request(f"/leads/{copper_id}", "DELETE", {})


def apply_cleanup(plan: dict) -> dict:
    """Applies the plan against real Copper. Per-lead try/except so one
    failure never aborts the rest. Returns copper_id lists per outcome."""
    result = {
        "stale_ok": [], "stale_failed": [],
        "test_deleted": [], "test_unqualified": [], "test_failed": [],
    }

    for row in plan["stale"]:
        cid = row["copper_id"]
        try:
            _set_unqualified(cid)
            result["stale_ok"].append(cid)
            print(f"  OK    set Unqualified: {row['company_name']}  [copper_id={cid}]")
        except Exception as exc:
            result["stale_failed"].append(cid)
            print(f"  FAIL  set Unqualified: {row['company_name']}  [copper_id={cid}] -- {exc!r}")

    for row in plan["test_records"]:
        cid = row["copper_id"]
        try:
            _delete_lead(cid)
            result["test_deleted"].append(cid)
            print(f"  OK    deleted test record: {row['company_name']}  [copper_id={cid}]")
        except Exception as exc:
            try:
                _set_unqualified(cid)
                result["test_unqualified"].append(cid)
                print(f"  OK    delete unavailable ({exc!r}); set Unqualified instead: "
                      f"{row['company_name']}  [copper_id={cid}]")
            except Exception as exc2:
                result["test_failed"].append(cid)
                print(f"  FAIL  could not delete or set Unqualified: {row['company_name']}  "
                      f"[copper_id={cid}] -- {exc2!r}")

    return result


# --- DB access + orchestration ------------------------------------------------

async def _fetch_plan(copper_user_id: Optional[int], owner_email: str) -> dict:
    copper_leads = fetch_open_leads_for_user(copper_user_id)
    async with AsyncSessionLocal() as db:
        app_leads = await rc.fetch_app_leads(db, owner_email)
    return plan_cleanup(copper_leads, app_leads)


# --- Rendering ------------------------------------------------------------

def render_plan(plan: dict, owner_email: str) -> str:
    lines = [
        "Close stale Copper leads",
        f"Owner: {owner_email}",
        "",
        f"=== Stale (Copper-open, archived in app) -> set Unqualified: {len(plan['stale'])} ===",
    ]
    for row in plan["stale"]:
        extra = f" ({row['sub_reason']})" if "sub_reason" in row else ""
        lines.append(f"  - {row['company_name']}  [copper_id={row['copper_id']}]{extra}")
    lines.append("")
    lines.append(f"=== Test records ({TEST_RECORD_PREFIX}*) -> delete: {len(plan['test_records'])} ===")
    for row in plan["test_records"]:
        lines.append(f"  - {row['company_name']}  [copper_id={row['copper_id']}]")
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------

def _check_env(copper_user_id: Optional[int]) -> None:
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
    if not copper_user_id and not settings.copper_user_id:
        missing.append("COPPER_USER_ID (or pass --copper-user-id)")
    if missing:
        print(f"BLOCKED: missing required Copper setting(s): {', '.join(missing)}. "
              f"Set them in the environment before running.")
        sys.exit(2)


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--copper-user-id", type=int, default=None,
                         help="Copper user id to scope to (default: settings.copper_user_id).")
    parser.add_argument("--owner", default=None,
                         help="App owner_email to reconcile against (default: settings.owner_email).")
    parser.add_argument("--commit", action="store_true",
                         help="Apply the writes. Without this flag, dry-run only (no writes).")
    args = parser.parse_args(argv)

    _check_env(args.copper_user_id)

    owner_email = args.owner or settings.owner_email
    plan = asyncio.run(_fetch_plan(args.copper_user_id, owner_email))

    print(render_plan(plan, owner_email))
    total = len(plan["stale"]) + len(plan["test_records"])

    if not args.commit:
        print(f"\nDRY RUN -- {total} lead(s) would be touched. Re-run with --commit to apply.")
        return 0

    if total == 0:
        print("\nNothing to do.")
        return 0

    print(f"\nApplying to {total} lead(s)...")
    result = apply_cleanup(plan)
    failed = len(result["stale_failed"]) + len(result["test_failed"])
    print(
        f"\nDone. {len(result['stale_ok'])} set Unqualified, "
        f"{len(result['test_deleted'])} deleted, "
        f"{len(result['test_unqualified'])} set Unqualified (delete unavailable), "
        f"{failed} failed."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
