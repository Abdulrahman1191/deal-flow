"""
Portfolio Intelligence — read/write API for the GP-feedback-driven feature.

Three resources:
  - companies (FUNDED / PASSED / NOT_SEEN)
  - outcomes (time-series of status changes per company)
  - signals  (controlled-vocabulary insights per company)

Owner-only (settings.owner_email) for v1. See PORTFOLIO_INTELLIGENCE_PLAN.md
for the canonical taxonomy + design rationale.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.portfolio import (
    DECISION_VALUES,
    DIRECTION_VALUES,
    OUTCOME_STATES,
    SIGNAL_TYPES,
    PortfolioCompany,
    PortfolioOutcome,
    PortfolioSignal,
)
from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _owner_or_403(user: User) -> None:
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")


# ----- schemas -----


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sector: Optional[str] = Field(default=None, max_length=64)
    region: Optional[str] = Field(default=None, max_length=64)
    founder_names: Optional[list[str]] = None
    website: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    our_decision: str  # FUNDED | PASSED | NOT_SEEN
    decision_at: Optional[date] = None
    decision_rationale: Optional[str] = None
    invested_amount_usd: Optional[int] = None
    initial_status: Optional[str] = "too_early"


class CompanyOut(BaseModel):
    id: str
    name: str
    sector: Optional[str]
    region: Optional[str]
    founder_names: Optional[list[str]]
    website: Optional[str]
    description: Optional[str]
    our_decision: str
    decision_at: Optional[date]
    decision_rationale: Optional[str]
    invested_amount_usd: Optional[int]
    current_status: str
    last_reviewed_at: Optional[datetime]
    created_at: datetime
    signal_count: int = 0
    outcome_count: int = 0


class OutcomeIn(BaseModel):
    status: str
    current_valuation_usd: Optional[int] = None
    last_round_stage: Optional[str] = None
    notes: Optional[str] = None


class OutcomeOut(BaseModel):
    id: str
    status: str
    recorded_at: datetime
    current_valuation_usd: Optional[int]
    last_round_stage: Optional[str]
    notes: Optional[str]


class SignalIn(BaseModel):
    signal_type: str
    direction: str
    weight: int = Field(ge=1, le=5)
    observed_at: Optional[date] = None
    note: Optional[str] = None


class SignalOut(BaseModel):
    id: str
    signal_type: str
    direction: str
    weight: int
    observed_at: Optional[date]
    note: Optional[str]
    created_at: datetime


class CompanyDetail(CompanyOut):
    outcomes: list[OutcomeOut] = []
    signals: list[SignalOut] = []


@router.get("/vocab")
async def vocab(user: User = Depends(get_current_user)):
    """Controlled vocabularies for the UI. Frontend renders dropdowns from here
    so we don't fork the truth between client + server."""
    _owner_or_403(user)
    return {
        "decisions": sorted(DECISION_VALUES),
        "outcomes": sorted(OUTCOME_STATES),
        "signal_types": sorted(SIGNAL_TYPES),
        "directions": sorted(DIRECTION_VALUES),
    }


def _company_to_out(c: PortfolioCompany) -> CompanyOut:
    return CompanyOut(
        id=str(c.id),
        name=c.name,
        sector=c.sector,
        region=c.region,
        founder_names=c.founder_names,
        website=c.website,
        description=c.description,
        our_decision=c.our_decision,
        decision_at=c.decision_at,
        decision_rationale=c.decision_rationale,
        invested_amount_usd=c.invested_amount_usd,
        current_status=c.current_status,
        last_reviewed_at=c.last_reviewed_at,
        created_at=c.created_at,
        signal_count=len(c.signals) if isinstance(c.signals, list) else 0,
        outcome_count=len(c.outcomes) if isinstance(c.outcomes, list) else 0,
    )


@router.get("/companies", response_model=list[CompanyOut])
async def list_companies(
    decision: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    q = select(PortfolioCompany).options(
        selectinload(PortfolioCompany.signals),
        selectinload(PortfolioCompany.outcomes),
    ).order_by(desc(PortfolioCompany.created_at)).limit(limit)
    if decision:
        q = q.where(PortfolioCompany.our_decision == decision)
    if status:
        q = q.where(PortfolioCompany.current_status == status)
    result = await db.execute(q)
    return [_company_to_out(c) for c in result.scalars().all()]


@router.post("/companies", response_model=CompanyOut, status_code=201)
async def create_company(
    body: CompanyIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    if body.our_decision not in DECISION_VALUES:
        raise HTTPException(status_code=400, detail=f"our_decision must be one of {sorted(DECISION_VALUES)}")
    initial = body.initial_status or "too_early"
    if initial not in OUTCOME_STATES:
        raise HTTPException(status_code=400, detail=f"initial_status must be one of {sorted(OUTCOME_STATES)}")

    company = PortfolioCompany(
        name=body.name.strip(),
        sector=body.sector,
        region=body.region,
        founder_names=body.founder_names,
        website=body.website,
        description=body.description,
        our_decision=body.our_decision,
        decision_at=body.decision_at,
        decision_rationale=body.decision_rationale,
        invested_amount_usd=body.invested_amount_usd,
        current_status=initial,
        last_reviewed_at=datetime.now(timezone.utc),
    )
    db.add(company)
    await db.flush()

    db.add(PortfolioOutcome(company_id=company.id, status=initial))
    await db.commit()
    await db.refresh(company, ["signals", "outcomes"])
    return _company_to_out(company)


@router.get("/companies/{company_id}", response_model=CompanyDetail)
async def get_company(
    company_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    r = await db.execute(
        select(PortfolioCompany)
        .options(selectinload(PortfolioCompany.outcomes), selectinload(PortfolioCompany.signals))
        .where(PortfolioCompany.id == company_id)
    )
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")
    base = _company_to_out(c)
    return CompanyDetail(
        **base.model_dump(),
        outcomes=[
            OutcomeOut(
                id=str(o.id), status=o.status, recorded_at=o.recorded_at,
                current_valuation_usd=o.current_valuation_usd,
                last_round_stage=o.last_round_stage, notes=o.notes,
            )
            for o in c.outcomes
        ],
        signals=[
            SignalOut(
                id=str(s.id), signal_type=s.signal_type, direction=s.direction,
                weight=s.weight, observed_at=s.observed_at, note=s.note,
                created_at=s.created_at,
            )
            for s in c.signals
        ],
    )


@router.patch("/companies/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: str,
    body: CompanyIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    r = await db.execute(
        select(PortfolioCompany).options(
            selectinload(PortfolioCompany.signals),
            selectinload(PortfolioCompany.outcomes),
        ).where(PortfolioCompany.id == company_id)
    )
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")

    if body.our_decision not in DECISION_VALUES:
        raise HTTPException(status_code=400, detail="our_decision invalid")

    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "initial_status":
            continue
        setattr(c, k, v)
    c.last_reviewed_at = datetime.now(timezone.utc)
    c.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(c, ["signals", "outcomes"])
    return _company_to_out(c)


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(
    company_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    r = await db.execute(select(PortfolioCompany).where(PortfolioCompany.id == company_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(c)
    await db.commit()


@router.post("/companies/{company_id}/outcomes", response_model=OutcomeOut, status_code=201)
async def add_outcome(
    company_id: str,
    body: OutcomeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    if body.status not in OUTCOME_STATES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(OUTCOME_STATES)}")
    r = await db.execute(select(PortfolioCompany).where(PortfolioCompany.id == company_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")

    outcome = PortfolioOutcome(
        company_id=c.id, status=body.status,
        current_valuation_usd=body.current_valuation_usd,
        last_round_stage=body.last_round_stage,
        notes=body.notes,
    )
    db.add(outcome)
    c.current_status = body.status
    c.last_reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(outcome)
    return OutcomeOut(
        id=str(outcome.id), status=outcome.status, recorded_at=outcome.recorded_at,
        current_valuation_usd=outcome.current_valuation_usd,
        last_round_stage=outcome.last_round_stage, notes=outcome.notes,
    )


@router.post("/companies/{company_id}/signals", response_model=SignalOut, status_code=201)
async def add_signal(
    company_id: str,
    body: SignalIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    if body.signal_type not in SIGNAL_TYPES:
        raise HTTPException(status_code=400, detail=f"signal_type must be one of {sorted(SIGNAL_TYPES)}")
    if body.direction not in DIRECTION_VALUES:
        raise HTTPException(status_code=400, detail="direction must be POSITIVE or NEGATIVE")

    r = await db.execute(select(PortfolioCompany).where(PortfolioCompany.id == company_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")

    signal = PortfolioSignal(
        company_id=c.id, signal_type=body.signal_type, direction=body.direction,
        weight=body.weight, observed_at=body.observed_at, note=body.note,
    )
    db.add(signal)
    c.last_reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(signal)
    return SignalOut(
        id=str(signal.id), signal_type=signal.signal_type, direction=signal.direction,
        weight=signal.weight, observed_at=signal.observed_at, note=signal.note,
        created_at=signal.created_at,
    )


@router.delete("/companies/{company_id}/signals/{signal_id}", status_code=204)
async def delete_signal(
    company_id: str,
    signal_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    r = await db.execute(
        select(PortfolioSignal).where(
            PortfolioSignal.id == signal_id, PortfolioSignal.company_id == company_id
        )
    )
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(s)
    await db.commit()


@router.get("/stats")
async def portfolio_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    from sqlalchemy import text

    rows = (await db.execute(text("""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE our_decision = 'FUNDED') AS funded,
          COUNT(*) FILTER (WHERE our_decision = 'PASSED') AS passed,
          COUNT(*) FILTER (WHERE our_decision = 'NOT_SEEN') AS not_seen,
          COUNT(*) FILTER (WHERE current_status = 'exited') AS exited,
          COUNT(*) FILTER (WHERE current_status = 'growing') AS growing,
          COUNT(*) FILTER (WHERE current_status = 'stalled') AS stalled,
          COUNT(*) FILTER (WHERE current_status = 'zombie') AS zombie,
          COUNT(*) FILTER (WHERE current_status = 'failed') AS failed,
          COUNT(*) FILTER (WHERE current_status = 'too_early') AS too_early
        FROM portfolio_companies
    """))).first()

    signal_pairs = (await db.execute(text("""
        SELECT signal_type, direction, COUNT(*) AS n
        FROM portfolio_signals
        GROUP BY 1, 2 ORDER BY n DESC LIMIT 20
    """))).all()

    return {
        "totals": {
            "total": rows[0] or 0,
            "by_decision": {"funded": rows[1] or 0, "passed": rows[2] or 0, "not_seen": rows[3] or 0},
            "by_status": {
                "exited": rows[4] or 0, "growing": rows[5] or 0,
                "stalled": rows[6] or 0, "zombie": rows[7] or 0,
                "failed": rows[8] or 0, "too_early": rows[9] or 0,
            },
        },
        "top_signals": [
            {"signal_type": r[0], "direction": r[1], "count": r[2]}
            for r in signal_pairs
        ],
    }
