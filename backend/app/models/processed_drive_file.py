from __future__ import annotations
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProcessedDriveFile(Base):
    """Bookkeeping for the inbound-folder sweep (issue #189).

    Most files in the "Inbound Pitch Decks" folder match no lead on their
    first pass (CVs, event brochures, decks for companies not yet in Copper).
    Without recording that outcome, every unmatched PDF gets re-downloaded
    and re-extracted on every 30-minute sweep forever -- this table is what
    lets the sweep skip a file it's already seen. `outcome='unmatched'` rows
    are re-checked after `settings.inbound_deck_recheck_days` (or `force`),
    since a lead can arrive in Copper after its deck did; other outcomes
    (`matched`) are never re-checked.
    """

    __tablename__ = "processed_drive_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    drive_file_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
