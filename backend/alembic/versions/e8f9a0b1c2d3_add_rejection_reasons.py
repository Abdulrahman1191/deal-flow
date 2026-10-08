"""add rejection_reasons column to assessment_cards

Issue #225: a thumbs-down on a YES lead auto-rejects it, carrying the
partner's reason tags forward so the rejection email / Copper write-back can
reuse them without retyping. Nullable -- only ever set by that one path.

This revision was briefly deleted by #229 (which mistook it for a duplicate
of a3b4c5d6e7f8's superset migration) after production had already applied
it, which left prod stamped at a revision no longer on disk. Restored by
#230 with an idempotent upgrade() so it's safe to re-run against a database
that already has the column, and a3b4c5d6e7f8 now chains after it instead
of branching from the same parent.

Revision ID: e8f9a0b1c2d3
Revises: c5e89de040a4
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "c5e89de040a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assessment_cards "
        "ADD COLUMN IF NOT EXISTS rejection_reasons JSONB"
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "rejection_reasons")
