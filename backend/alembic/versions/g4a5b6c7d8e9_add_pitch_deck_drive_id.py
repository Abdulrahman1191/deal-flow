"""add pitch_deck_drive_id column to leads

Revision ID: g4a5b6c7d8e9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g4a5b6c7d8e9"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Pitch deck PDFs now live in a shared Google Drive folder rather than on
    a local volume. We store the Drive file ID per lead so the View PDF
    endpoint can redirect to drive.google.com/file/d/<id>/view.

    Existing rows get NULL — backfilled by scripts/sync_drive_to_db.py once
    the user finishes uploading the 427 PDFs into the Drive folder.
    """
    op.add_column(
        "leads",
        sa.Column("pitch_deck_drive_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leads", "pitch_deck_drive_id")
