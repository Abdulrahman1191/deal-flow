"""add index on assessment_overrides(acted_by_email, created_at)

Backs GET /overrides/my-reasons (issue #152), which does a bounded
ORDER BY created_at DESC ... LIMIT query scoped to a single
acted_by_email -- without this index that's a full-table scan + sort.

Revision ID: s6f7a8b9c0d1
Revises: r5e6f7a8b9c0
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op


revision: str = "s6f7a8b9c0d1"
down_revision: Union[str, None] = "r5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_assessment_overrides_email_created_at",
        "assessment_overrides",
        ["acted_by_email", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assessment_overrides_email_created_at", table_name="assessment_overrides")
