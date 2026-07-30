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

# Internal QA account used to poke leads while testing — its rows are
# synthetic, not real deal flow, so every calibration aggregate excludes it.
_TEST_ACCOUNT_EMAIL = "almuhammed@raed.vc"


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """`numerator / denominator`, rounded for chart display. None when the
    denominator is 0 (an empty bucket has no meaningful rate, not a 0% one)."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


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


class WeeklyAgreementOut(BaseModel):
    week_start: datetime
    total: int
    agreements: int
    agreement_rate: float


class PartnerProfileOut(BaseModel):
    acted_by_email: str
    total: int
    agreement_rate: float
    confirm_rate: float
    correction_rate: float
    rate_down_rate: float
    articulation_rate: float


class CalibrationStatsOut(BaseModel):
    total_rows: int
    agreements: int
    disagreements: int
    agreement_rate: Optional[float]
    agreement_over_time: list[WeeklyAgreementOut]
    partner_profiles: list[PartnerProfileOut]
    disagreement_pairs: dict[str, int]
    excluded_test_account: str


@router.get("/calibration", response_model=CalibrationStatsOut)
async def calibration_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Backend for the calibration dashboard (issue #104): is the AI
    improving over time, and which partners give articulated ratings vs
    vague ones. Read-only, owner-only. Every aggregate here excludes
    `_TEST_ACCOUNT_EMAIL` — its leads are QA test data, not real deal flow.
    """
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    from sqlalchemy import text

    params = {"test_email": _TEST_ACCOUNT_EMAIL}

    overall = (await db.execute(text("""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ai_bucket = human_bucket) AS agreements,
          COUNT(*) FILTER (WHERE ai_bucket != human_bucket) AS disagreements
        FROM assessment_overrides
        WHERE acted_by_email IS DISTINCT FROM :test_email
    """), params)).first()

    weekly_rows = (await db.execute(text("""
        SELECT
          date_trunc('week', created_at) AS week_start,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ai_bucket = human_bucket) AS agreements
        FROM assessment_overrides
        WHERE acted_by_email IS DISTINCT FROM :test_email
        GROUP BY 1
        ORDER BY 1
    """), params)).all()

    partner_rows = (await db.execute(text("""
        SELECT
          acted_by_email,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ai_bucket = human_bucket) AS agreements,
          COUNT(*) FILTER (WHERE trigger = 'confirm') AS confirms,
          COUNT(*) FILTER (WHERE trigger IN ('override', 're-override')) AS corrections,
          COUNT(*) FILTER (WHERE trigger = 'rate_down') AS rate_downs,
          COUNT(*) FILTER (
            WHERE (human_reason_tags IS NOT NULL AND human_reason_tags != '[]'::jsonb)
               OR (human_reason IS NOT NULL AND human_reason != '')
          ) AS articulated
        FROM assessment_overrides
        WHERE acted_by_email IS DISTINCT FROM :test_email
          AND acted_by_email IS NOT NULL
        GROUP BY acted_by_email
        ORDER BY total DESC
    """), params)).all()

    pair_rows = (await db.execute(text("""
        SELECT ai_bucket || '→' || human_bucket AS pair, COUNT(*) AS n
        FROM assessment_overrides
        WHERE trigger IN ('override','re-override')
          AND acted_by_email IS DISTINCT FROM :test_email
        GROUP BY 1 ORDER BY 2 DESC
    """), params)).all()

    total = overall[0] or 0
    agreements = overall[1] or 0
    disagreements = overall[2] or 0

    return CalibrationStatsOut(
        total_rows=total,
        agreements=agreements,
        disagreements=disagreements,
        agreement_rate=_rate(agreements, total),
        agreement_over_time=[
            WeeklyAgreementOut(
                week_start=r[0],
                total=r[1],
                agreements=r[2],
                agreement_rate=_rate(r[2], r[1]) or 0.0,
            )
            for r in weekly_rows
        ],
        partner_profiles=[
            PartnerProfileOut(
                acted_by_email=r[0],
                total=r[1],
                agreement_rate=_rate(r[2], r[1]) or 0.0,
                confirm_rate=_rate(r[3], r[1]) or 0.0,
                correction_rate=_rate(r[4], r[1]) or 0.0,
                rate_down_rate=_rate(r[5], r[1]) or 0.0,
                articulation_rate=_rate(r[6], r[1]) or 0.0,
            )
            for r in partner_rows
        ],
        disagreement_pairs={r[0]: r[1] for r in pair_rows},
        excluded_test_account=_TEST_ACCOUNT_EMAIL,
    )
