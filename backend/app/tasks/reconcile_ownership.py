from __future__ import annotations
"""
Periodic ownership-reconcile beat task (issue #123).

sync_one_user (app/tasks/sync_copper.py) only reassigns a lead when Copper
currently reports it as *open*-status-assigned to a user this pass is
actively syncing (via fetch_open_leads_for_user, which is filtered to both
assignee_ids and copper_open_status_id). A lead whose Copper assignee changed
but that isn't in the open status -- or whose new assignee isn't yet an
active Dealflow user -- is never re-fetched, so owner_email silently drifts
from Copper's actual current assignee until someone runs the manual
reconcile_ownership report/--fix (issue #114) by hand.

This task closes that gap: it walks EVERY Dealflow lead with a copper_id,
regardless of status, and corrects owner_email to match Copper's current
assignee whenever they differ and the assignee resolves to a known user.
Firm-wide, idempotent (a lead already matching Copper is left untouched), and
one-way (Copper -> Dealflow only; never writes back to Copper). Shares its
comparison/fix logic with the manual CLI via app/services/ownership.py.
"""
import asyncio

from app.database import CelerySessionLocal
from app.services.copper_service import fetch_all_leads
from app.services.ownership import apply_fix, fetch_app_leads, fetch_copper_user_map, find_mismatches
from app.tasks.celery_app import celery


async def _run() -> dict:
    copper_leads = fetch_all_leads()
    copper_index = {str(c.get("id", "")): c for c in copper_leads}
    async with CelerySessionLocal() as db:
        app_leads = await fetch_app_leads(db)
        copper_user_map = await fetch_copper_user_map(db)
        mismatches = find_mismatches(app_leads, copper_index, copper_user_map)
        fixed = await apply_fix(db, mismatches, log_events=True)
    unresolved = len(mismatches) - len(fixed)
    print(
        f"[reconcile_ownership] mismatches={len(mismatches)} fixed={len(fixed)} "
        f"unresolved={unresolved}"
    )
    return {"mismatches": len(mismatches), "fixed": len(fixed), "unresolved": unresolved}


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def reconcile_ownership_task(self) -> dict:
    """Beat task: firm-wide, status-agnostic ownership reconcile against Copper."""
    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)
