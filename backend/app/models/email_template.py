from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailTemplate(Base):
    """One row per (language, template_key) -- the four rejection email
    templates (issue #232: CONFLICT/MARKET_SIZE/TRACTION/MANDATE, en/ar),
    moved out of Python constants so copy can change without a deploy and
    the change reaches every unsent lead with no backfill (issue #234).

    `body` keeps the same {greeting_line}/{company}/{reason}/{partner_name}
    placeholders the #232 constants used; `reason_text` is the clause/
    sentence substituted into {reason}.
    """

    __tablename__ = "email_templates"
    __table_args__ = (
        UniqueConstraint("language", "template_key", name="uq_email_templates_language_template_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    template_key: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EmailTemplateVersion(Base):
    """One row per prior save of an EmailTemplate -- also written on revert
    (the state about to be overwritten is snapshotted first), so any earlier
    subject/body/reason_text combination can be restored as a single unit.
    """

    __tablename__ = "email_template_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    template_key: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    saved_by: Mapped[Optional[str]] = mapped_column(String(255))
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
