from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.feedback import Feedback
from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/feedback", tags=["feedback"])


def _owner_or_403(user: User) -> None:
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")


class FeedbackIn(BaseModel):
    message: str = Field(min_length=2, max_length=4000)
    page_url: Optional[str] = Field(default=None, max_length=512)
    category: Optional[str] = Field(default=None, max_length=32)


class FeedbackOut(BaseModel):
    id: str
    user_email: str
    page_url: Optional[str]
    category: Optional[str]
    message: str
    resolved_at: Optional[datetime]
    created_at: datetime

    @classmethod
    def from_row(cls, fb: Feedback) -> "FeedbackOut":
        return cls(
            id=str(fb.id),
            user_email=fb.user_email,
            page_url=fb.page_url,
            category=fb.category,
            message=fb.message,
            resolved_at=fb.resolved_at,
            created_at=fb.created_at,
        )


@router.post("", response_model=FeedbackOut, status_code=201)
async def submit_feedback(
    body: FeedbackIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fb = Feedback(
        user_email=user.email,
        page_url=body.page_url,
        category=body.category,
        message=body.message.strip(),
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    return FeedbackOut.from_row(fb)


@router.get("", response_model=list[FeedbackOut])
async def list_feedback(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Admin inbox — every submitted feedback item, across all users."""
    _owner_or_403(user)
    result = await db.execute(select(Feedback).order_by(Feedback.created_at.desc()))
    return [FeedbackOut.from_row(fb) for fb in result.scalars().all()]


@router.post("/{feedback_id}/resolve", response_model=FeedbackOut)
async def resolve_feedback(
    feedback_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="Not found")
    fb.resolved_at = datetime.now(timezone.utc) if not fb.resolved_at else None
    await db.commit()
    await db.refresh(fb)
    return FeedbackOut.from_row(fb)
