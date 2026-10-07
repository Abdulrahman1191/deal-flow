from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Boolean, func
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
    # Evidenced hard metrics (issue #221) -- at most 4 short strings ("$220K
    # GMV", "550 vendors"), as stated about the company itself. Null on rows
    # written before this column existed; AssessmentOut normalises that to
    # [] rather than surfacing a null traction row.
    traction: Mapped[Optional[list]] = mapped_column(JSONB)
    scoring_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    draft_subject: Mapped[Optional[str]] = mapped_column(Text)
    draft_body: Mapped[Optional[str]] = mapped_column(Text)
    draft_type: Mapped[Optional[str]] = mapped_column(String(16))
    # Bucket the current draft_subject/draft_body/draft_type were actually
    # written for (issue #150). Set on a successful regen, nulled out on a
    # failed one, so a stale draft is detectable rather than inferred.
    draft_bucket: Mapped[Optional[str]] = mapped_column(String(16))
    research_sources: Mapped[Optional[list]] = mapped_column(JSONB)
    data_gaps: Mapped[Optional[list]] = mapped_column(JSONB)
    # Raw Tavily research dict — captured so we can reconstruct what the AI saw
    # at assessment time when later promoting overrides into training data.
    research_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Snapshot of which Raed-portfolio precedents were retrieved + cited
    # in this assessment. Foundation for measuring retrieval-quality vs accuracy.
    precedents_cited: Mapped[Optional[list]] = mapped_column(JSONB)
    # True when this score was made without a pitch deck -- website content
    # and/or description only (issue #144) -- so the UI can flag it as
    # lower-confidence until a deck is attached and re-assessment refines it.
    assessed_without_deck: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # sha256 of every input that fed the assessor's prompt when this card was
    # written -- company name, description, pitch deck text, and (deck-less
    # leads only) scraped website content. The assessor runs at
    # temperature=0, so an unchanged fingerprint guarantees a re-run would
    # return the identical verdict (issue #203's bulk-reassess endpoint uses
    # this to skip leads with nothing new to re-score). Null on cards written
    # before this column existed -- app/services/bulk_reassess.py treats a
    # null fingerprint as "changed" rather than guessing.
    input_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
    user_override: Mapped[Optional[str]] = mapped_column(String(16))
    user_override_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Canonical UNQUAL_REASON_OPTIONS labels the partner picked when
    # regenerating a rejection draft (issue #223) -- a human choice that
    # outranks claude_agent.generate_unqualification_reason when the lead is
    # later archived/sent (see claude_agent.resolve_unqualification_reason).
    # Null until a reasoned regeneration happens; cleared back to null if the
    # bucket is overridden away from REJECT, so a stale selection can't leak
    # into a future, unrelated rejection.
    rejection_reasons: Mapped[Optional[list]] = mapped_column(JSONB)
    # Lightweight thumbs up/down on the AI recommendation ("up" | "down"),
    # distinct from a bucket override. Persisted so the UI shows the active thumb.
    user_rating: Mapped[Optional[str]] = mapped_column(String(8))
    user_rating_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Reason tags carried forward from a thumbs-down that auto-rejected a YES
    # lead (issue #225), so the rejection email / Copper write-back can reuse
    # them without the partner re-typing. Null for every other path.
    rejection_reasons: Mapped[Optional[list]] = mapped_column(JSONB)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped["Lead"] = relationship("Lead", back_populates="assessment")
