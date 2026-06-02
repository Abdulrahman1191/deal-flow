from __future__ import annotations
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.briefing import DailyBriefing
from app.models.user import User
from app.schemas.briefing import BriefingOut, PaginatedBriefings
from app.services.auth import get_current_user
from app.tasks.generate_briefing import generate_briefing_task

router = APIRouter(prefix="/briefings", tags=["briefings"])


@router.get("/today", response_model=BriefingOut)
async def get_today(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(DailyBriefing).where(DailyBriefing.date == date.today()))
    briefing = result.scalar_one_or_none()
    if not briefing:
        raise HTTPException(status_code=404, detail="Today's briefing not yet generated")
    return briefing


@router.get("", response_model=PaginatedBriefings)
async def list_briefings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    total = (await db.execute(select(func.count()).select_from(DailyBriefing))).scalar()
    result = await db.execute(
        select(DailyBriefing).order_by(DailyBriefing.date.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return PaginatedBriefings(total=total, page=page, page_size=page_size, items=result.scalars().all())


@router.get("/{briefing_date}", response_model=BriefingOut)
async def get_by_date(
    briefing_date: date,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(DailyBriefing).where(DailyBriefing.date == briefing_date))
    briefing = result.scalar_one_or_none()
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found for this date")
    return briefing


@router.post("/generate", status_code=202)
async def trigger_briefing(_: User = Depends(get_current_user)):
    generate_briefing_task.delay()
    return {"status": "queued"}
