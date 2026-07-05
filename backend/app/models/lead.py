from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import String, Text, ARRAY, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    copper_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    # Per-user ownership: the @raed.vc email of the user this lead belongs to
    # (the Copper assignee). Leads are scoped to their owner across the app.
    owner_email: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[Optional[str]] = mapped_column(String(512))
    description: Mapped[Optional[str]] = mapped_column(Text)
    stage: Mapped[Optional[str]] = mapped_column(String(64))
    region: Mapped[Optional[str]] = mapped_column(String(128))
    founder_names: Mapped[Optional[List]] = mapped_column(ARRAY(Text))
    linkedin_urls: Mapped[Optional[List]] = mapped_column(ARRAY(Text))
    company_linkedin_url: Mapped[Optional[str]] = mapped_column(String(512))
    copper_person_id: Mapped[Optional[str]] = mapped_column(String(64))
    copper_company_id: Mapped[Optional[str]] = mapped_column(String(64))
    copper_opportunity_id: Mapped[Optional[str]] = mapped_column(String(64))
    pitch_deck_filename: Mapped[Optional[str]] = mapped_column(String(255))
    pitch_deck_text: Mapped[Optional[str]] = mapped_column(Text)
    pitch_deck_ingested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    pitch_deck_s3: Mapped[Optional[str]] = mapped_column(String(512))
    # Google Drive file ID for the lead's pitch deck PDF.
    # Populated by scripts/sync_drive_to_db.py after the user uploads PDFs
    # to the shared Drive folder. View endpoint redirects to
    # https://drive.google.com/file/d/<id>/view.
    pitch_deck_drive_id: Mapped[Optional[str]] = mapped_column(String(64))
    raw_copper_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    assessment: Mapped[Optional["AssessmentCard"]] = relationship("AssessmentCard", back_populates="lead", uselist=False)

    def __repr__(self) -> str:
        return f"<Lead {self.company_name} [{self.status}]>"
