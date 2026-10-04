from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BulkRejectionBatchItem(Base):
    """One row per lead in a POST /leads/bulk-send-rejection batch (issue
    #205). Unlike bulk-archive/bulk-reassign, the actual work (one email send
    + finalize per lead) happens in a throttled Celery task dispatched well
    after the request returns -- so unlike those endpoints' synchronous
    response counts, there's no way to report sent/failed/skipped at request
    time. This table is written once per lead when the batch is accepted
    (status="queued" or "skipped" for leads that failed the send-time
    eligibility re-check) and updated by the task as each send resolves, so
    GET /leads/bulk-send-rejection/{batch_id} can report a partial batch
    instead of guessing.
    """

    __tablename__ = "bulk_rejection_batch_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # The partner who ran the batch -- scopes GET /bulk-send-rejection/{id} so
    # one admin can't read another's batch results.
    owner_email: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    # queued -> sent | failed | skipped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
