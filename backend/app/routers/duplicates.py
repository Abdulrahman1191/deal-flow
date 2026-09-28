from __future__ import annotations
"""
Owner-only duplicate-lead review surface (issue #180). Detect-and-flag only:
no endpoint here ever merges, archives, or auto-resolves a cluster. Archiving
a duplicate reuses the existing POST /leads/{id}/archive-no-reply or
DELETE /leads/{id} endpoints; this router adds the two actions those don't
cover -- reassign and dismiss.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.lead import Lead
from app.models.user import User
from app.schemas.duplicates import (
    DismissRequest,
    DismissResult,
    DuplicateClusterOut,
    DuplicateLeadOut,
    ReassignRequest,
    ReassignResult,
)
from app.services import copper_writer
from app.services.auth import get_current_user, is_owner
from app.services.duplicates import applicant_email, cluster_key, dismiss_cluster, list_duplicate_clusters
from app.services.events import EVENT_REASSIGNED, log_event

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


def _require_owner(user: User) -> None:
    if not is_owner(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("", response_model=list[DuplicateClusterOut])
async def get_duplicate_clusters(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Firm-wide duplicate clusters, newest first, excluding dismissed ones.
    Owner-only -- this surfaces leads across every partner's board."""
    _require_owner(user)
    clusters = await list_duplicate_clusters(db)
    return [
        DuplicateClusterOut(
            cluster_key=cluster_key(l.id for l in c["leads"]),
            match_reasons=c["match_reasons"],
            signal_count=c["signal_count"],
            leads=[
                DuplicateLeadOut(
                    id=l.id,
                    company_name=l.company_name,
                    owner_email=l.owner_email,
                    status=l.status,
                    copper_id=l.copper_id,
                    created_at=l.created_at,
                    applicant_email=applicant_email(l) or None,
                )
                for l in c["leads"]
            ],
        )
        for c in clusters
    ]


@router.post("/dismiss", response_model=DismissResult)
async def dismiss_duplicate_cluster(
    body: DismissRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Marks the exact lead-id set as "not a duplicate" so it stops
    appearing. Only reappears if a genuinely new lead joins the cluster
    (which changes its cluster_key -- see app.services.duplicates)."""
    _require_owner(user)
    row = await dismiss_cluster(db, body.lead_ids, dismissed_by=user.email)
    return DismissResult(cluster_key=row.cluster_key, dismissed_by=row.dismissed_by, dismissed_at=row.dismissed_at)


@router.post("/reassign", response_model=ReassignResult)
async def reassign_duplicate_lead(
    body: ReassignRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Manual, explicit, single-lead owner reassignment for the duplicate
    review flow -- never auto-resolves a cluster. Firm-wide lookup (not
    scoped to the caller's own leads): this is how the owner moves a
    cross-owner duplicate to its correct partner. Best-effort pushes the new
    assignee to Copper when the target owner's Copper user id is known."""
    _require_owner(user)

    result = await db.execute(select(Lead).where(Lead.id == body.lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    from_owner = lead.owner_email
    new_owner = body.owner_email.strip().lower()
    lead.owner_email = new_owner
    await log_event(
        db, lead.id, EVENT_REASSIGNED,
        {"from_owner": from_owner, "to_owner": new_owner, "source": "manual_duplicate_review", "by": user.email},
    )
    await db.commit()

    copper_pushed = False
    if lead.copper_id:
        target = (await db.execute(select(User).where(User.email == new_owner))).scalar_one_or_none()
        if target and target.copper_user_id:
            try:
                copper_writer.push_assignee(lead.copper_id, target.copper_user_id)
                copper_pushed = True
            except Exception as exc:
                print(f"[duplicates] best-effort Copper assignee push failed for lead {lead.id}: {exc!r}")

    return ReassignResult(
        lead_id=lead.id, from_owner=from_owner, to_owner=new_owner, copper_push_enqueued=copper_pushed,
    )
