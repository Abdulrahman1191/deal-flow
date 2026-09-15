"""add leads.last_assessment_error + assessment_failed_redrives

Persist *why* an assessment failed (issue #163): assess_lead_task previously
only print()ed the exception in _mark_failed, so a lead dead-lettered to
'failed' couldn't be diagnosed from the app or DB -- exactly what stranded
367 leads firm-wide with no visible root cause. last_assessment_error /
last_assessment_error_at are set on every failure path and cleared on the
next clean outcome. assessment_failed_redrives tracks how many times
redrive_failed_assessments_task has auto-requeued this lead, capped by
settings.assessment_failed_max_redrives.

Revision ID: v9c0d1e2f3a4
Revises: u8b9c0d1e2f3
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "v9c0d1e2f3a4"
down_revision: Union[str, None] = "u8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("last_assessment_error", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column("last_assessment_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("assessment_failed_redrives", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("leads", "assessment_failed_redrives")
    op.drop_column("leads", "last_assessment_error_at")
    op.drop_column("leads", "last_assessment_error")
