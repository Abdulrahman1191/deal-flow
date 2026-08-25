"""add leads.copper_unqualified_at

Tracks whether the lead's Copper record currently reads Unqualified from an
AI-driven rejection write-back (app/routers/assessments.py::_finalize_sent).
override_bucket clears it once it corrects the disposition on a
REJECT->YES/MAYBE transition (issue #157), so that correction fires exactly
once per Unqualified write instead of being sent on every later override.

Revision ID: u8b9c0d1e2f3
Revises: t7a8b9c0d1e2
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u8b9c0d1e2f3"
down_revision: Union[str, None] = "t7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("copper_unqualified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leads", "copper_unqualified_at")
