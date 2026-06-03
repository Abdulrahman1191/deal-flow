from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssessmentCard(Base):
    __tablename__ = "assessment_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"))
    bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    positive_signals: Mapped[Optional[list]] = mapped_column(JSONB)
    red_flags: Mapped[Optional[list]] = mapped_column(JSONB)
    scoring_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    draft_subject: Mapped[Optional[str]] = mapped_column(Text)
    draft_body: Mapped[Optional[str]] = mapped_column(Text)
    draft_type: Mapped[Optional[str]] = mapped_column(String(16))
    research_sources: Mapped[Optional[list]] = mapped_column(JSONB)
    data_gaps: Mapped[Optional[list]] = mapped_column(JSONB)
    # Raw Tavily research dict — captured so we can reconstruct what the AI saw
    # at assessment time when later promoting overrides into training data.
    research_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Snapshot of which Raed-portfolio precedents were retrieved + cited
    # in this assessment. Foundation for measuring retrieval-quality vs accuracy.
    precedents_cited: Mapped[Optional[list]] = mapped_column(JSONB)
    user_override: Mapped[Optional[str]] = mapped_column(String(16))
    user_override_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Lightweight thumbs up/down on the AI recommendation ("up" | "down"),
    # distinct from a bucket override. Persisted so the UI shows the active thumb.
    user_rating: Mapped[Optional[str]] = mapped_column(String(8))
    user_rating_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped["Lead"] = relationship("Lead", back_populates="assessment")
