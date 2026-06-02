"""add pitch deck columns

Revision ID: 2965ca37fed7
Revises: 588f26f9a4d0
Create Date: 2026-05-13 09:57:57.307108

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2965ca37fed7'
down_revision: Union[str, None] = '588f26f9a4d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("pitch_deck_filename", sa.String(length=255), nullable=True))
    op.add_column("leads", sa.Column("pitch_deck_text", sa.Text, nullable=True))
    op.add_column(
        "leads",
        sa.Column("pitch_deck_ingested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leads", "pitch_deck_ingested_at")
    op.drop_column("leads", "pitch_deck_text")
    op.drop_column("leads", "pitch_deck_filename")
