"""
Read-only access to the training-data table.

Owner-only. Used by:
  - the eval harness (`scripts/eval_prompts.py`)
  - future analytical UI / metrics dashboard
  - hand inspection ("did we capture that one?")
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.override import AssessmentOverride
from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/overrides", tags=["overrides"])


class OverrideOut(BaseModel):
    id: str
    lead_id: str
    assessment_id: str
    ai_bucket: str
    ai_confidence: Optional[int]
    ai_summary: Optional[str]
    human_bucket: str
    trigger: str
    disagreement: bool
    has_research: bool
    has_deck: bool
    created_at: datetime


@router.get("", response_model=list[OverrideOut])
async def list_overrides(
    only_disagreements: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    query = select(AssessmentOverride).order_by(AssessmentOverride.created_at.desc()).limit(limit)
    if only_disagreements:
        query = query.where(AssessmentOverride.ai_bucket != AssessmentOverride.human_bucket)

    result = await db.execute(query)
    rows = result.scalars().all()
    return [
        OverrideOut(
            id=str(r.id),
            lead_id=str(r.lead_id),
            assessment_id=str(r.assessment_id),
            ai_bucket=r.ai_bucket,
            ai_confidence=r.ai_confidence,
            ai_summary=r.ai_summary,
            human_bucket=r.human_bucket,
            trigger=r.trigger,
            disagreement=r.ai_bucket != r.human_bucket,
            has_research=r.research_snap is not None,
            has_deck=bool(r.deck_excerpt),
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/stats")
async def override_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Aggregate metrics for the LLM-tuning loop. Cheap query — uses indexes."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    from sqlalchemy import text

    rows = (await db.execute(text("""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ai_bucket = human_bucket) AS agreements,
          COUNT(*) FILTER (WHERE ai_bucket != human_bucket) AS disagreements,
          COUNT(*) FILTER (WHERE trigger = 'override') AS first_overrides,
          COUNT(*) FILTER (WHERE trigger = 'approve') AS approves,
          COUNT(*) FILTER (WHERE trigger = 'skip') AS skips,
          COUNT(*) FILTER (WHERE trigger = 're-override') AS re_overrides,
          COUNT(*) FILTER (WHERE research_snap IS NOT NULL) AS with_research_snap
        FROM assessment_overrides
    """))).first()

    by_pair = (await db.execute(text("""
        SELECT ai_bucket || '→' || human_bucket AS pair, COUNT(*) AS n
        FROM assessment_overrides
        WHERE trigger IN ('override','re-override')
        GROUP BY 1 ORDER BY 2 DESC
    """))).all()

    total = rows[0] or 0
    agree = rows[1] or 0
    accuracy = round(100.0 * agree / total, 1) if total else None

    return {
        "total_rows": total,
        "agreements": agree,
        "disagreements": rows[2] or 0,
        "implied_accuracy_pct": accuracy,
        "by_trigger": {
            "override": rows[3] or 0,
            "approve": rows[4] or 0,
            "skip": rows[5] or 0,
            "re_override": rows[6] or 0,
        },
        "with_research_snapshot": rows[7] or 0,
        "override_pairs": {r[0]: r[1] for r in by_pair},
    }
