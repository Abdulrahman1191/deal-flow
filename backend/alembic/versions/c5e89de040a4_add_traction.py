"""add traction column to assessment_cards

Issue #221: structured field for evidenced hard metrics ("$220K GMV", "550
vendors") so the card can surface traction without the UI parsing it out of a
signal sentence. Nullable -- existing cards have no traction field and
AssessmentOut normalises that to [] at read time rather than backfilling.

Revision ID: c5e89de040a4
Revises: d4e5f6a7b8c9
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "c5e89de040a4"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_cards",
        sa.Column("traction", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "traction")
