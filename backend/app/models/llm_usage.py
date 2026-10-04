from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LLMUsage(Base):
    """One row per DeepSeek call, written by claude_agent._chat_completion
    (issue #191) so "why is our token spend high" and "are assessments
    paused" are answerable from GET /api/v1/ops/llm-usage instead of by
    reading source code or DeepSeek's own unattributed dashboard total.

    `lead_id` is deliberately a plain column, not a foreign key: this is an
    analytics trail, not a relationship, and it must survive a lead being
    deleted. Written via the same best-effort sync-engine pattern as
    task_heartbeat/_mark_failed -- a failed write must never fail the
    DeepSeek call it's measuring.
    """

    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # assess / verify_deck / draft_regen / unqual_reason / linkedin_pick / briefing
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ok / error / breaker_open
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
