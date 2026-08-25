from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LeadActionLog(Base):
    """Snapshot of a reversible disposition, so POST /leads/{id}/undo can
    restore exactly what it overwrote rather than guessing a default
    (issue #153). One row per undoable action.

    Scoped for now to the archive paths: `archive_no_reply` (leads.py) and
    `archive_after_send` (the rejection-send archive in
    assessments.py::_finalize_sent). Bucket-override/approve/bulk-archive
    undo, and a full multi-step undo history, are deferred follow-ups --
    /undo only ever reverses the single most recent row per lead.
    """

    __tablename__ = "lead_action_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_email: Mapped[Optional[str]] = mapped_column(String(255))

    # lead.status + the exact Copper tag set, captured right before the
    # action overwrote them. Undo restores from here instead of a default.
    prior_state: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # True when this archive followed an actual email send (the
    # rejection-send path) -- undo must still restore app/Copper state but
    # can never unsend the email, and the response must say so.
    email_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # The copper_outbox row id the original action enqueued, if any -- undo
    # cancels it first if it's still pending, so a delayed original write
    # can never land after the reversal and re-archive the lead.
    copper_outbox_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))

    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
