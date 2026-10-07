"""add rejection_reasons column to assessment_cards

Issue #225: a thumbs-down on a YES lead auto-rejects it, carrying the
partner's reason tags forward so the rejection email / Copper write-back can
reuse them without retyping. Nullable -- only ever set by that one path.

Revision ID: e8f9a0b1c2d3
Revises: c5e89de040a4
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "c5e89de040a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_cards",
        sa.Column("rejection_reasons", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "rejection_reasons")
