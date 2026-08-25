"""add assessment_cards.draft_bucket

Tracks which bucket a draft's `draft_subject`/`draft_body`/`draft_type` were
actually written for (issue #150). A bucket override that fails to regenerate
the draft now nulls out the draft fields instead of silently leaving a draft
written for the old bucket in place -- `draft_bucket` records the last bucket
a *successful* regeneration matched, so staleness is detectable rather than
inferred. Nullable -- existing rows and any draft written before this column
existed have no recorded bucket.

Revision ID: s6f7a8b9c0d1
Revises: r5e6f7a8b9c0
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s6f7a8b9c0d1"
down_revision: Union[str, None] = "r5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_cards",
        sa.Column("draft_bucket", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "draft_bucket")
