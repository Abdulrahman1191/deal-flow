"""
Copper sync integration test — on DISPOSABLE test leads only.

Proves the two write-backs against the real Copper API without touching any real
lead, then cleans up after itself:

  REJECT  : create a throwaway lead -> apply the archive/reject write
            (status -> Unqualified) -> verify the lead's status changed.
  MEETING : create a throwaway lead -> convert_lead_to_opportunity ->
            verify an Opportunity was created in the configured pipeline.

Every record it creates is named with a TEST_MARKER and deleted at the end
(unless --keep). Talks directly to Copper (no app DB needed), so set a dummy
DATABASE_URL just to satisfy config import.

Run (owner — writes to YOUR Copper, but only disposable test records):
  DATABASE_URL='postgresql+asyncpg://x:x@localhost/x' \
  COPPER_API_KEY=... COPPER_USER_EMAIL=... COPPER_USER_ID=... \
  COPPER_OPEN_STATUS_ID=... COPPER_UNQUALIFIED_STATUS_ID=... \
  COPPER_PIPELINE_ID=... COPPER_PIPELINE_STAGE_ID=... \
  /tmp/eval-venv/bin/python scripts/test_copper_sync.py            # creates+tests+cleans
  ... scripts/test_copper_sync.py --keep                          # leave records for manual inspection
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings
from app.services import copper_writer
from app.services.copper_service import COPPER_BASE, _headers, fetch_lead_by_id

TEST_MARKER = "ZZZ-RAED-SYNC-TEST"
KEEP = "--keep" in sys.argv

_results = []
def check(name, ok, detail=""):
    _results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def _create_lead(label: str) -> str | None:
    body = {
        "name": f"{TEST_MARKER} {label}",
        "assignee_id": settings.copper_user_id or None,
        "status_id": settings.copper_open_status_id or None,
        "email": {"email": "raed-sync-test@example.com", "category": "work"},
        "company_name": f"{TEST_MARKER} {label} Co",
    }
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{COPPER_BASE}/leads", headers=_headers(), json=body)
        r.raise_for_status()
        return str(r.json().get("id"))


def _delete(path: str):
    try:
        with httpx.Client(timeout=20) as c:
            c.delete(f"{COPPER_BASE}{path}", headers=_headers())
    except Exception as exc:
        print(f"     (cleanup {path} failed: {exc!r})")


def main():
    print(f"== Copper sync test (marker={TEST_MARKER}, cleanup={'OFF' if KEEP else 'ON'}) ==\n")
    print(f"config: unqualified_status={settings.copper_unqualified_status_id} "
          f"pipeline={settings.copper_pipeline_id} stage={settings.copper_pipeline_stage_id}\n")
    created_leads, created_opps, created_people, created_companies = [], [], [], []

    # --- REJECT ---
    print("REJECT flow:")
    a = None
    try:
        a = _create_lead("REJECT"); created_leads.append(a)
        check("created disposable lead", bool(a), f"copper_id={a}")
        payload = {"tags": ["raed:archived"], "status_id": settings.copper_unqualified_status_id}
        copper_writer.execute_copper_request(f"/leads/{a}", "PUT", payload)
        after = fetch_lead_by_id(a) or {}
        ok = str(after.get("status_id")) == str(settings.copper_unqualified_status_id)
        check("lead moved to Unqualified status", ok,
              f"status_id now {after.get('status_id')} (want {settings.copper_unqualified_status_id})")
    except Exception as exc:
        check("REJECT flow", False, repr(exc)[:160])

    # --- MEETING ---
    print("\nMEETING flow:")
    b = None
    try:
        b = _create_lead("MEETING"); created_leads.append(b)
        check("created disposable lead", bool(b), f"copper_id={b}")
        res = copper_writer.convert_lead_to_opportunity(b, f"{TEST_MARKER} MEETING Co", "Test Founder")
        opp = (res or {}).get("opportunity_id")
        check("convert returned an opportunity_id", bool(opp), f"opportunity_id={opp}")
        if opp:
            created_opps.append(opp)
            if (res or {}).get("person_id"): created_people.append(res["person_id"])
            if (res or {}).get("company_id"): created_companies.append(res["company_id"])
            with httpx.Client(timeout=20) as c:
                r = c.get(f"{COPPER_BASE}/opportunities/{opp}", headers=_headers())
            od = r.json() if r.status_code == 200 else {}
            ok = str(od.get("pipeline_id")) == str(settings.copper_pipeline_id)
            check("opportunity is in the configured pipeline", ok,
                  f"pipeline_id={od.get('pipeline_id')} (want {settings.copper_pipeline_id})")
    except Exception as exc:
        check("MEETING flow", False, repr(exc)[:160])

    # --- cleanup ---
    if KEEP:
        print(f"\n--keep set: leaving {len(created_leads)} lead(s), {len(created_opps)} opp(s) in Copper for inspection.")
    else:
        print("\nCleaning up test records...")
        for oid in created_opps: _delete(f"/opportunities/{oid}")
        for pid in created_people: _delete(f"/people/{pid}")
        for cid in created_companies: _delete(f"/companies/{cid}")
        for lid in created_leads: _delete(f"/leads/{lid}")  # converted lead may already be gone (ignored)
        print("cleanup done.")

    passed = sum(1 for r in _results if r); total = len(_results)
    print(f"\nRESULT: {passed}/{total} checks passed — "
          + ("ALL GREEN ✓" if passed == total else "see FAILs above"))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
