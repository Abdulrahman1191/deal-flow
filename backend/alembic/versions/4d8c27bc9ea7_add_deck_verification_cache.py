"""add deck_verification_cache table

Caches pitch_deck.verify_match_candidates' DeepSeek verdicts (issue #192
item 1), keyed on (drive_file_id, lead_id), so sync_pitch_decks' 30-minute
sweep stops re-verifying the same unmatched (Drive file, lead) pair forever.
A hit requires the stored deck_text_hash to still match and verified_at to
be within settings.deck_verification_cache_ttl_days.

Revision ID: 4d8c27bc9ea7
Revises: a7b8c9d0e1f2
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "4d8c27bc9ea7"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deck_verification_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("drive_file_id", sa.String(128), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deck_text_hash", sa.String(64), nullable=False),
        sa.Column("is_match", sa.Boolean, nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("drive_file_id", "lead_id", name="uq_deck_verification_cache_file_lead"),
    )


def downgrade() -> None:
    op.drop_table("deck_verification_cache")
