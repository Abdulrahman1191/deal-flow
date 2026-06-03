"""add user_rating to assessment_cards

Revision ID: i6c7d8e9f0a1
Revises: h5b6c7d8e9f0
Create Date: 2026-06-03

Lightweight thumbs up/down on the AI's recommendation, distinct from a bucket
override. "up" = the human confirms the AI got it right; "down" = the human
disagrees. Both are captured as training rows in `assessment_overrides` (triggers
"confirm" / "rate_down"); this column just persists the current rating so the UI
can show the active thumb across reloads.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i6c7d8e9f0a1"
down_revision: Union[str, None] = "h5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_cards",
        sa.Column("user_rating", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "assessment_cards",
        sa.Column("user_rating_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "user_rating_at")
    op.drop_column("assessment_cards", "user_rating")
