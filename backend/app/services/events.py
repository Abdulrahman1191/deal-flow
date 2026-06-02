from __future__ import annotations
"""Append-only event log for leads. One row = one meaningful state change."""
import uuid
from typing import Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import LeadEvent


# Known event types. Strings, not an Enum, so the column stays
# forward-compatible without a migration.
EVENT_ASSESSED = "assessed"
EVENT_BUCKET_OVERRIDDEN = "bucket_overridden"
EVENT_DRAFT_APPROVED = "draft_approved"
EVENT_EMAIL_SENT = "email_sent"
EVENT_ARCHIVED = "archived"
EVENT_ARCHIVED_NO_REPLY = "archived_no_reply"
EVENT_CONVERTED = "converted"
EVENT_COPPER_UPDATED = "copper_updated"


async def log_event(
    db: AsyncSession,
    lead_id: Union[str, uuid.UUID],
    event_type: str,
    payload: Optional[dict] = None,
) -> None:
    """Append a row to lead_events. Caller is responsible for commit."""
    if isinstance(lead_id, str):
        lead_id = uuid.UUID(lead_id)
    db.add(LeadEvent(lead_id=lead_id, event_type=event_type, payload=payload))
