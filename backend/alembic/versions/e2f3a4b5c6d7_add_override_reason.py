"""add human_reason fields to assessment_overrides

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-05-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_overrides",
        sa.Column("human_reason_tags", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "assessment_overrides",
        sa.Column("human_reason", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_overrides", "human_reason")
    op.drop_column("assessment_overrides", "human_reason_tags")
