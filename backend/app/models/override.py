from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AssessmentOverride(Base):
    """Snapshot of an AI assessment paired with the human's final decision.

    Captured automatically on three triggers:
      - "override": user explicitly clicked a different bucket chip
      - "approve":  user clicked Approve (implicit YES confirmation if AI said YES)
      - "skip":     user clicked Skip ⤬ (implicit REJECT confirmation if AI didn't say REJECT)
      - "send":     reserved for future — user actually sent the email

    The `research_snap` + `deck_excerpt` fields are critical for future training:
    we need to know what the AI SAW when it made its call, not just what it said.
    Without them, three months from now this row tells us nothing actionable.
    """

    __tablename__ = "assessment_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_cards.id", ondelete="CASCADE"), nullable=False)

    ai_bucket: Mapped[str] = mapped_column(String(8), nullable=False)
    ai_confidence: Mapped[Optional[int]] = mapped_column(Integer)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)
    ai_breakdown: Mapped[Optional[Any]] = mapped_column(JSONB)

    human_bucket: Mapped[str] = mapped_column(String(8), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)

    research_snap: Mapped[Optional[Any]] = mapped_column(JSONB)
    deck_excerpt: Mapped[Optional[str]] = mapped_column(Text)

    # Human-supplied reason for the decision. Tags are a controlled vocabulary
    # (e.g. ["Marketplace model", "Not MENA"]) — easy to feed back into the
    # prompt as labelled signal. Free-text `human_reason` is bonus context.
    human_reason_tags: Mapped[Optional[Any]] = mapped_column(JSONB)
    human_reason: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
