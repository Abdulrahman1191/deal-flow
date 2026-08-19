"""
Associates performance — GP-facing view of lead-management throughput
(issue #125). Admin-only. One row per client-facing associate, computed with
a single grouped SQL aggregate (not row-by-row iteration).

Distinct from the calibration dashboard (#104/#105), which measures rating
*quality*; this measures pipeline *throughput* — how many leads an associate
owns and how far they've moved them through the board.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/associates", tags=["associates"])

# The four client-facing associates the GP wants visibility into.
# almuhammed@raed.vc is deliberately excluded — non-client-facing, test leads
# only (see issue #125).
ASSOCIATE_EMAILS = [
    "abdulrahman@raed.vc",
    "waleed@raed.vc",
    "uday@raed.vc",
    "yomna@raed.vc",
]

class AssociatePerformanceOut(BaseModel):
    email: str
    leads_total: int
    backlog: int
    awaiting_deck: int
    active: int
    outreach_sent: int
    approved: int
    converted: int
    archived: int


class AssociatesPerformanceOut(BaseModel):
    associates: list[AssociatePerformanceOut]


def _zero_row(email: str) -> AssociatePerformanceOut:
    return AssociatePerformanceOut(
        email=email, leads_total=0, backlog=0, awaiting_deck=0, active=0,
        outreach_sent=0, approved=0, converted=0, archived=0,
    )


@router.get("/performance", response_model=AssociatesPerformanceOut)
async def associates_performance(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-associate lead-management counts for the GP dashboard. Admin-only."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    email_params = {f"email{i}": email for i, email in enumerate(ASSOCIATE_EMAILS)}
    email_placeholders = ", ".join(f":{key}" for key in email_params)

    rows = (await db.execute(text(f"""
        WITH sent_counts AS (
          SELECT l.owner_email AS owner_email, COUNT(DISTINCT ac.lead_id) AS outreach_sent
          FROM assessment_cards ac
          JOIN leads l ON l.id = ac.lead_id
          WHERE ac.sent_at IS NOT NULL AND l.owner_email IN ({email_placeholders})
          GROUP BY l.owner_email
        )
        SELECT
          l.owner_email,
          COUNT(*) AS leads_total,
          COUNT(*) FILTER (WHERE l.status IN ('pending', 'processing')) AS backlog,
          COUNT(*) FILTER (WHERE l.status = 'awaiting_deck') AS awaiting_deck,
          COUNT(*) FILTER (WHERE l.status NOT IN ('pending', 'processing', 'awaiting_deck', 'approved', 'archived')) AS active,
          COALESCE(sc.outreach_sent, 0) AS outreach_sent,
          COUNT(*) FILTER (WHERE l.status = 'approved') AS approved,
          COUNT(*) FILTER (WHERE l.copper_opportunity_id IS NOT NULL) AS converted,
          COUNT(*) FILTER (WHERE l.status = 'archived') AS archived
        FROM leads l
        LEFT JOIN sent_counts sc ON sc.owner_email = l.owner_email
        WHERE l.owner_email IN ({email_placeholders})
        GROUP BY l.owner_email, sc.outreach_sent
    """), email_params)).all()

    by_email = {
        r[0]: AssociatePerformanceOut(
            email=r[0], leads_total=r[1], backlog=r[2], awaiting_deck=r[3],
            active=r[4], outreach_sent=r[5], approved=r[6], converted=r[7],
            archived=r[8],
        )
        for r in rows
    }

    return AssociatesPerformanceOut(
        associates=[by_email.get(email, _zero_row(email)) for email in ASSOCIATE_EMAILS]
    )
