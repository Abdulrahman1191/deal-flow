"""add lead_action_log.batch_id + card_id

Extends the undo mechanism (issue #153) to bucket-override, approve, and
bulk-archive (issue #159):
  - `card_id` lets a bucket-override/approve undo restore the EXACT
    assessment card the action changed, rather than "whichever card is
    latest now" -- if a reassessment created a new card since, undo can
    detect that drift instead of clobbering the new card's state.
  - `batch_id` groups the per-lead lead_action_log rows a single
    POST /leads/bulk-archive call writes, so
    POST /leads/bulk-archive/{batch_id}/undo can reverse the whole batch in
    one call.

Revision ID: w0e1f2a3b4c5
Revises: v9c0d1e2f3a4
Create Date: 2026-09-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "w0e1f2a3b4c5"
down_revision: Union[str, None] = "v9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lead_action_log",
        sa.Column("card_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_cards.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "lead_action_log",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_lead_action_log_batch_id", "lead_action_log", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_action_log_batch_id", table_name="lead_action_log")
    op.drop_column("lead_action_log", "batch_id")
    op.drop_column("lead_action_log", "card_id")
