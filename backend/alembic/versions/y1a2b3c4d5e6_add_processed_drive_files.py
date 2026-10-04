"""add processed_drive_files table

Issue #189: the inbound-folder sweep must not re-download/re-extract the same
unmatched Drive file every 30 minutes forever (most files in that folder
match no lead on their first pass). This table tracks the outcome of every
PDF the sweep has already looked at, keyed on the Drive file id.

Revision ID: y1a2b3c4d5e6
Revises: x1e2f3a4b5c6
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "y1a2b3c4d5e6"
down_revision: Union[str, None] = "x1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_drive_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("drive_file_id", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_processed_drive_files_drive_file_id", "processed_drive_files", ["drive_file_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_processed_drive_files_drive_file_id", table_name="processed_drive_files")
    op.drop_table("processed_drive_files")
