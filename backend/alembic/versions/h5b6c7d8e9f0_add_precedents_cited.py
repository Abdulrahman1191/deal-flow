"""add precedents_cited column to assessment_cards

Revision ID: h5b6c7d8e9f0
Revises: g4a5b6c7d8e9
Create Date: 2026-06-03

Stores the list of historical portfolio precedents that were retrieved and
cited in each AI assessment. Format: list of {company, score, verdict}.

This is the foundation for the next loop: once we have enough overrides, we
can ask "did citing Precedent X lead to better or worse calibration?" and
weight the retrieval accordingly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "h5b6c7d8e9f0"
down_revision: Union[str, None] = "g4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_cards",
        sa.Column("precedents_cited", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "precedents_cited")
