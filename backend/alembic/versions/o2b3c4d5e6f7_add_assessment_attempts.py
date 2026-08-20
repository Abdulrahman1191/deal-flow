"""add leads.assessment_attempts

Per-lead assess_lead_task attempt counter (issue #129). Lets the task bound
the worker-crash-loop: acks_late + task_reject_on_worker_lost redelivers a
task that crashes the worker forever, bypassing Celery's own max_retries,
since worker-lost redeliveries never touch self.request.retries. This column
is incremented at the start of every attempt (redeliveries included) and
reset to 0 on a clean outcome, so a lead that never succeeds is dead-lettered
to 'failed' after MAX_ASSESS_ATTEMPTS instead of looping indefinitely.

Revision ID: o2b3c4d5e6f7
Revises: n1a2b3c4d5e6
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "o2b3c4d5e6f7"
down_revision: Union[str, None] = "n1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("assessment_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("leads", "assessment_attempts")
