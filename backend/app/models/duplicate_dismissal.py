from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import List

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DuplicateDismissal(Base):
    __tablename__ = "duplicate_dismissals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Stable key for the exact set of leads in a dismissed cluster -- the
    # sorted, pipe-joined lead ids (see app/services/duplicates.py::cluster_key).
    # If the cluster later gains a new member its key changes, so the bigger
    # cluster reappears for review rather than staying silently dismissed
    # forever (issue #180 acceptance criterion).
    cluster_key: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False, index=True)
    lead_ids: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False)
    dismissed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
