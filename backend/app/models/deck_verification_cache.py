from __future__ import annotations
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeckVerificationCache(Base):
    """One row per (Drive file, lead) pair ever checked by
    pitch_deck.verify_match_candidates (issue #192 item 1).

    Without this, the same near-miss candidate is re-verified via DeepSeek on
    every 30-minute sync_pitch_decks sweep, forever, always reaching the same
    conclusion -- the 2026-09-30 breaker outage traced ~51k failing calls/day
    largely to this path. A hit requires both `deck_text_hash` to still match
    (a file replaced in place under the same Drive id is a genuinely new
    deck) and `verified_at` to be within settings.deck_verification_cache_ttl_days.

    `lead_id` is a plain column, not a foreign key, matching LLMUsage's
    choice: this is a verdict cache, not a relationship, and must tolerate
    stale rows after a lead is deleted (the next verification attempt just
    overwrites them).
    """

    __tablename__ = "deck_verification_cache"
    __table_args__ = (
        UniqueConstraint("drive_file_id", "lead_id", name="uq_deck_verification_cache_file_lead"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    drive_file_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deck_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
