from __future__ import annotations
import uuid
from datetime import datetime, date
from typing import Optional

from sqlalchemy import Text, Date, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailyBriefing(Base):
    __tablename__ = "daily_briefings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    top_themes: Mapped[list] = mapped_column(JSONB, nullable=False)
    deep_dives: Mapped[list] = mapped_column(JSONB, nullable=False)
    raw_research: Mapped[Optional[str]] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
