from __future__ import annotations
import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.models.user import User
from app.services.copper_service import (
    fetch_open_leads_for_user,
    lookup_user_id,
    map_copper_lead,
)
from app.tasks.celery_app import celery
from app.tasks.assess_lead import assess_lead_task


def _disabled() -> bool:
    return os.getenv("DISABLE_COPPER_SYNC", "").lower() in ("1", "true", "yes")


async def resolve_copper_id(db: AsyncSession, user: User) -> int | None:
    """Return the user's Copper user id, resolving + caching it on first use.

    Uses the shared Copper API key to look the user up by their @raed.vc email.
    Returns None (and leaves the cache null, to retry next cycle) if the user has
    no Copper account — they simply get an empty board.
    """
    if user.copper_user_id:
        return user.copper_user_id
    try:
        cid = lookup_user_id(user.email)
    except Exception as exc:  # no Copper user / API error
        print(f"[sync_copper] no Copper user for {user.email}: {exc!r}")
        return None
    user.copper_user_id = cid
    await db.commit()
    return cid


async def sync_one_user(db: AsyncSession, user: User) -> dict:
    """Import + reconcile one user's open-assigned Copper leads, scoped to them."""
    cid = await resolve_copper_id(db, user)
    if not cid:
        return {"user": user.email, "skipped": "no copper id"}

    raw_leads = fetch_open_leads_for_user(cid)
    if settings.test_lead_limit > 0:
        raw_leads = raw_leads[: settings.test_lead_limit]

    # Safety valve: an empty fetch is far more likely a Copper hiccup than the
    # user genuinely having every lead unassigned — never mass-archive on it.
    if not raw_leads:
        return {"user": user.email, "skipped": "copper returned 0 leads"}

    copper_ids = {str(r.get("id", "")) for r in raw_leads if r.get("id")}

    new_count = 0
    for raw in raw_leads:
        copper_id = str(raw.get("id", ""))
        if not copper_id:
            continue
        existing = await db.execute(select(Lead).where(Lead.copper_id == copper_id))
        if existing.scalar_one_or_none():
            continue
        lead = Lead(**map_copper_lead(raw), owner_email=user.email)
        db.add(lead)
        await db.flush()
        assess_lead_task.delay(str(lead.id))
        new_count += 1

    # Archive this user's active leads no longer open-assigned to them in Copper.
    stale_count = 0
    result = await db.execute(
        select(Lead).where(
            Lead.owner_email == user.email,
            Lead.copper_id.is_not(None),
            Lead.status != "archived",
        )
    )
    for lead in result.scalars().all():
        if lead.copper_id in copper_ids:
            continue
        lead.status = "archived"
        stale_count += 1
        try:
            from app.services.events import log_event, EVENT_ARCHIVED
            await log_event(db, lead.id, EVENT_ARCHIVED,
                            {"reason": "copper_reconcile: no longer open-assigned to user"})
        except Exception as exc:
            print(f"[sync_copper] event log skipped for {lead.id}: {exc!r}")

    await db.commit()
    print(f"[sync_copper] user={user.email} imported={new_count} archived_stale={stale_count} copper_open={len(raw_leads)}")
    return {"user": user.email, "synced": new_count, "archived_stale": stale_count, "checked": len(raw_leads)}


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def sync_copper_leads_task(self) -> dict:
    """Beat task: reconcile EVERY app user's Copper-assigned leads."""
    if _disabled():
        return {"skipped": "DISABLE_COPPER_SYNC env var is set"}
    try:
        return asyncio.run(_run_all())
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_all() -> dict:
    results = []
    async with CelerySessionLocal() as db:
        users = (await db.execute(select(User).where(User.is_active == True))).scalars().all()  # noqa: E712
        for user in users:
            try:
                results.append(await sync_one_user(db, user))
            except Exception as exc:
                print(f"[sync_copper] sync failed for {user.email}: {exc!r}")
                results.append({"user": user.email, "error": repr(exc)[:200]})
    return {"users": len(results), "results": results}


@celery.task(bind=True, max_retries=3, default_retry_delay=30)
def sync_user_copper_leads_task(self, email: str) -> dict:
    """On-demand: sync a single user's leads (called when they open the board)."""
    if _disabled():
        return {"skipped": "DISABLE_COPPER_SYNC env var is set"}
    try:
        return asyncio.run(_run_one(email))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_one(email: str) -> dict:
    email = email.strip().lower()
    async with CelerySessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user:
            return {"skipped": f"no user {email}"}
        return await sync_one_user(db, user)
