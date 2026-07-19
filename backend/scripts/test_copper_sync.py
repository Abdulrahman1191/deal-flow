"""
Copper live diagnostic — on-demand, maintainer-run proof that the accept-meeting
and reject write-backs actually reach the REAL Copper API. This is the live
counterpart to backend/tests/test_copper_writebacks.py (Copper mocked, runs in
CI); this script is NEVER run in CI or on a schedule — it needs a real
COPPER_API_KEY and creates real (if disposable) Copper records.

Two modes:

  --check   Read-only connectivity check: confirms COPPER_API_KEY authenticates
            (GET /account) and COPPER_USER_EMAIL/COPPER_USER_ID resolve to a
            real Copper user. Makes NO writes. Run this first to sanity-check
            credentials before doing a full run.

  (default) Full round trip. For each flow, creates ONE disposable lead marked
            with TEST_MARKER and drives the SAME code the app uses in
            production — not a hand-built payload:
              REJECT  : copper_writer.archive_in_copper() (enqueues to the
                        copper_outbox table, same as a real reject) ->
                        drain_copper_outbox_task() (the real drain worker,
                        called synchronously so the write actually flushes) ->
                        re-fetch the lead from Copper and confirm status_id ==
                        the configured Unqualified status.
              MEETING : copper_writer.convert_lead_to_opportunity() (this one
                        is synchronous in production, not routed through the
                        outbox) -> confirm the resulting opportunity landed in
                        the configured pipeline.
            Because REJECT goes through the real outbox, this run needs a
            reachable DATABASE_URL pointing at a Postgres with the app's
            schema (copper_outbox table) — point it at the real app DB (or a
            copy of it), not a throwaway/dummy URL.

Cleanup is bulletproof: every record is tracked in a module-level list the
INSTANT it's created (before anything else can fail), and an atexit handler
plus a `finally` block both call cleanup() — so a failed assertion, an
unexpected exception, or Ctrl-C during the run still deletes every lead /
opportunity / person / company it created. Pass --keep to leave them for
manual inspection instead (a previous version of this script left a stray
`ZZZ-RAED-SYNC-TEST` lead behind on a failed run; that should no longer be
possible).

Run (maintainer — writes to YOUR Copper, but only disposable test records):
  DATABASE_URL=<real app db url> \
  COPPER_API_KEY=... COPPER_USER_EMAIL=... COPPER_USER_ID=... \
  COPPER_OPEN_STATUS_ID=... COPPER_UNQUALIFIED_STATUS_ID=... \
  COPPER_PIPELINE_ID=... COPPER_PIPELINE_STAGE_ID=... \
  python scripts/test_copper_sync.py --check   # connectivity only, no writes
  python scripts/test_copper_sync.py           # full round trip, cleans up after itself
  python scripts/test_copper_sync.py --keep    # full round trip, leaves records for inspection
"""
from __future__ import annotations

import atexit
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings
from app.services import copper_writer
from app.services.copper_service import COPPER_BASE, _headers, fetch_lead_by_id, lookup_user_id

TEST_MARKER = "ZZZ-COPPER-CHECK"
KEEP = "--keep" in sys.argv
CHECK_ONLY = "--check" in sys.argv

_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def _require_or_exit(required: dict) -> None:
    """Refuse to run with a clear message when required Copper settings are
    unset, rather than failing confusingly deep inside an HTTP call."""
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"BLOCKED: missing required setting(s): {', '.join(missing)}. "
              f"Set them in the environment (see this script's module docstring) before running.")
        sys.exit(2)


# --- created-record tracking -------------------------------------------------
# Populated the instant each record is created (never batched at the end) so
# cleanup() always knows what to delete, no matter where a failure happens.
created_leads: list[str] = []
created_opps: list[str] = []
created_people: list[str] = []
created_companies: list[str] = []
_cleaned_up = False


def _delete(path: str) -> None:
    try:
        with httpx.Client(timeout=20) as c:
            c.delete(f"{COPPER_BASE}{path}", headers=_headers())
    except Exception as exc:
        print(f"     (cleanup {path} failed: {exc!r})")


def cleanup() -> None:
    """Idempotent; registered with atexit AND called from a `finally` so it
    runs on success, on a failed assertion, on an unhandled exception, and on
    Ctrl-C (KeyboardInterrupt still triggers atexit handlers)."""
    global _cleaned_up
    if _cleaned_up:
        return
    _cleaned_up = True
    if KEEP:
        print(f"\n--keep set: leaving {len(created_leads)} lead(s), {len(created_opps)} opp(s) in Copper for inspection.")
        return
    if not (created_leads or created_opps or created_people or created_companies):
        return
    print("\nCleaning up test records...")
    for oid in created_opps:
        _delete(f"/opportunities/{oid}")
    for pid in created_people:
        _delete(f"/people/{pid}")
    for cid in created_companies:
        _delete(f"/companies/{cid}")
    for lid in created_leads:
        _delete(f"/leads/{lid}")  # a converted lead is already gone in Copper; delete is a no-op then
    print("cleanup done.")


atexit.register(cleanup)


def _create_lead(label: str) -> str:
    body = {
        "name": f"{TEST_MARKER} {label}",
        "assignee_id": settings.copper_user_id or None,
        "status_id": settings.copper_open_status_id or None,
        "email": {"email": "copper-check@example.com", "category": "work"},
        "company_name": f"{TEST_MARKER} {label} Co",
    }
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{COPPER_BASE}/leads", headers=_headers(), json=body)
        r.raise_for_status()
        lead_id = str(r.json().get("id"))
    created_leads.append(lead_id)
    return lead_id


# --- --check: read-only connectivity mode ------------------------------------

def run_check_mode() -> None:
    print("== Copper connectivity check (read-only, no writes) ==\n")
    _require_or_exit({
        "COPPER_API_KEY": settings.copper_api_key,
        "COPPER_USER_EMAIL": settings.copper_user_email,
        "COPPER_USER_ID": settings.copper_user_id,
    })

    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{COPPER_BASE}/account", headers=_headers())
        check("API key authenticates (GET /account)", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as exc:
        check("API key authenticates (GET /account)", False, repr(exc)[:160])

    # Copper's API has no single-user GET; lookup_user_id paginates
    # /users/search (already used by the app at bootstrap) to resolve it.
    try:
        resolved_id = lookup_user_id(settings.copper_user_email)
        check("COPPER_USER_EMAIL resolves to a Copper user", True, f"user_id={resolved_id}")
        check("resolved user_id matches COPPER_USER_ID", resolved_id == settings.copper_user_id,
              f"resolved={resolved_id} configured={settings.copper_user_id}")
    except Exception as exc:
        check("COPPER_USER_EMAIL resolves to a Copper user", False, repr(exc)[:160])

    _print_result_and_exit()


# --- full round trip ----------------------------------------------------------

def _run_reject_flow() -> None:
    print("REJECT flow (real code path: copper_writer.archive_in_copper -> outbox -> drain):")
    try:
        lead_id = _create_lead("REJECT")
        check("created disposable lead", bool(lead_id), f"copper_id={lead_id}")

        existing = fetch_lead_by_id(lead_id) or {}
        copper_writer.archive_in_copper(lead_id, existing.get("tags"))

        from app.tasks.drain_outbox import drain_copper_outbox_task
        drain_result = drain_copper_outbox_task()
        check("outbox drained with no failures", drain_result.get("failed", 0) == 0, str(drain_result))

        after = fetch_lead_by_id(lead_id) or {}
        ok = str(after.get("status_id")) == str(settings.copper_unqualified_status_id)
        check("lead moved to Unqualified status", ok,
              f"status_id now {after.get('status_id')} (want {settings.copper_unqualified_status_id})")
    except Exception as exc:
        check("REJECT flow", False, repr(exc)[:160])


def _run_meeting_flow() -> None:
    print("\nMEETING flow (real code path: copper_writer.convert_lead_to_opportunity):")
    try:
        lead_id = _create_lead("MEETING")
        check("created disposable lead", bool(lead_id), f"copper_id={lead_id}")

        res = copper_writer.convert_lead_to_opportunity(lead_id, f"{TEST_MARKER} MEETING Co", "Test Founder")
        opp = (res or {}).get("opportunity_id")
        check("convert returned an opportunity_id", bool(opp), f"opportunity_id={opp}")
        if opp:
            created_opps.append(opp)
            if (res or {}).get("person_id"):
                created_people.append(res["person_id"])
            if (res or {}).get("company_id"):
                created_companies.append(res["company_id"])
            with httpx.Client(timeout=20) as c:
                r = c.get(f"{COPPER_BASE}/opportunities/{opp}", headers=_headers())
            od = r.json() if r.status_code == 200 else {}
            ok = str(od.get("pipeline_id")) == str(settings.copper_pipeline_id)
            check("opportunity is in the configured pipeline", ok,
                  f"pipeline_id={od.get('pipeline_id')} (want {settings.copper_pipeline_id})")
    except Exception as exc:
        check("MEETING flow", False, repr(exc)[:160])


def run_full_check() -> None:
    print(f"== Copper sync live diagnostic (marker={TEST_MARKER}, cleanup={'OFF' if KEEP else 'ON'}) ==\n")
    _require_or_exit({
        "COPPER_API_KEY": settings.copper_api_key,
        "COPPER_USER_EMAIL": settings.copper_user_email,
        "COPPER_USER_ID": settings.copper_user_id,
        "COPPER_OPEN_STATUS_ID": settings.copper_open_status_id,
        "COPPER_UNQUALIFIED_STATUS_ID": settings.copper_unqualified_status_id,
        "COPPER_PIPELINE_ID": settings.copper_pipeline_id,
        "COPPER_PIPELINE_STAGE_ID": settings.copper_pipeline_stage_id,
    })
    print(f"config: unqualified_status={settings.copper_unqualified_status_id} "
          f"pipeline={settings.copper_pipeline_id} stage={settings.copper_pipeline_stage_id}\n")

    try:
        _run_reject_flow()
        _run_meeting_flow()
    finally:
        cleanup()

    _print_result_and_exit()


def _print_result_and_exit() -> None:
    passed = sum(1 for r in _results if r)
    total = len(_results)
    print(f"\nRESULT: {passed}/{total} checks passed — " + ("ALL GREEN ✓" if passed == total else "see FAILs above"))
    sys.exit(0 if passed == total else 1)


def main() -> None:
    if CHECK_ONLY:
        run_check_mode()
    else:
        run_full_check()


if __name__ == "__main__":
    main()
