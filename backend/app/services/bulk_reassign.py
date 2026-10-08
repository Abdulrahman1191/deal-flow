from __future__ import annotations
"""
Admin-only bulk reassignment of an associate's leads (issue #194) -- for when
someone is out of office and their pipeline would otherwise sit untouched.

The one thing this has to get right: `reconcile_ownership_task`
(app/tasks/reconcile_ownership.py) snaps `owner_email` back to Copper's
current assignee_id every 5 minutes. A reassignment written only to our DB is
therefore silently reverted within 5 minutes -- so every reassignment here
must also enqueue a Copper assignee push (via copper_writer.push_assignee,
outbox-backed) for the write to stick. That's why execute_bulk_reassign
refuses upfront (400, nothing written) if any target lacks a copper_user_id.

Shared by both POST /leads/reassign/preview (read-only) and POST
/leads/reassign (the real thing) so the two can never drift: the preview's
count is exactly what execute will move given the same inputs.
"""
import uuid
from collections import Counter
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.lead import Lead
from app.models.user import User
from app.services.csv_export import effective_bucket

# "Active work only" -- the statuses a lead can be moved out of. Deliberately
# excludes `archived` (never moved) and `approved` (queued to send -- treated
# like a converted relationship, same as copper_opportunity_id/sent_at below).
ACTIVE_STATUSES = ["assessed", "awaiting_deck", "pending", "processing", "failed"]


async def matching_leads(
    db: AsyncSession,
    from_owner: str,
    buckets: Optional[list[str]] = None,
    include_converted: bool = False,
) -> list[Lead]:
    """Leads eligible to move off `from_owner`, sorted by id for a
    deterministic round-robin split. Never includes archived leads (outside
    ACTIVE_STATUSES). Excludes a lead with a sent email or a
    copper_opportunity_id (converted) -- those represent a relationship
    already owned by a person -- unless `include_converted` is explicitly
    set. `buckets` filters on the effective bucket (override-aware, same
    resolution csv_export/the kanban use) -- a lead matches if its effective
    bucket is in the list; `None` or an empty list matches every bucket.

    The owner_email scope is pushed into SQL (indexed column, narrows a
    firm-wide table down to one associate's rows); status/converted/sent/
    bucket are checked in Python after the fetch, mirroring bulk_archive's
    per-lead checks in app/routers/leads.py."""
    result = await db.execute(
        select(Lead)
        .options(selectinload(Lead.assessment))
        .where(Lead.owner_email == from_owner)
    )
    leads = result.scalars().all()

    matched = []
    for lead in leads:
        if lead.status not in ACTIVE_STATUSES:
            continue
        if not include_converted:
            if lead.copper_opportunity_id:
                continue
            if lead.assessment and lead.assessment.sent_at:
                continue
        if buckets and effective_bucket(lead.assessment) not in buckets:
            continue
        matched.append(lead)

    return sorted(matched, key=lambda l: l.id)


def round_robin_assignments(leads: list[Lead], to_owners: list[str]) -> dict[uuid.UUID, str]:
    """Deterministic even split: leads (already sorted by id) are handed out
    to to_owners in the order given, round-robin. Same inputs always produce
    the same split -- the preview's split and the execute's split can never
    disagree."""
    return {lead.id: to_owners[i % len(to_owners)] for i, lead in enumerate(leads)}


def breakdown_counts(leads: list[Lead], assignments: dict[uuid.UUID, str], to_owners: list[str]) -> dict:
    by_status = dict(Counter(lead.status for lead in leads))
    by_bucket = dict(Counter(effective_bucket(lead.assessment) or "unscored" for lead in leads))
    target_counts = Counter(assignments.values())
    by_target = {owner: target_counts.get(owner, 0) for owner in to_owners}
    return {"by_status": by_status, "by_bucket": by_bucket, "by_target": by_target}


async def resolve_targets(db: AsyncSession, to_owners: list[str]) -> tuple[dict[str, User], list[str]]:
    """Resolves every to_owners entry to a User with a copper_user_id.
    Returns (email -> User map, missing emails) -- missing names every
    to_owners entry that doesn't resolve to a known user or lacks a
    copper_user_id (the reconcile would bounce their leads right back, so
    the caller must refuse upfront rather than write anything)."""
    result = await db.execute(select(User).where(User.email.in_(to_owners)))
    found = {u.email: u for u in result.scalars().all()}
    missing = [email for email in to_owners if not found.get(email) or not found[email].copper_user_id]
    return found, missing
