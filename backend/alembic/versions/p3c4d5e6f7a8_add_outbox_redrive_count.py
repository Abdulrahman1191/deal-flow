"""add copper_outbox.redrive_count

Bounded auto re-drive for failed outbox rows (issue #131): a Copper write-back
that exhausts drain_outbox's 5 delivery attempts lands in status='failed' and,
without this, is never retried again -- a transient Copper outage permanently
strands it. redrive_failed_outbox_task increments this counter each time it
resets a failed row back to 'pending'; once it reaches
settings.outbox_max_redrives the row is left 'failed' for good instead of
looping forever.

Revision ID: p3c4d5e6f7a8
Revises: o2b3c4d5e6f7
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p3c4d5e6f7a8"
down_revision: Union[str, None] = "o2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "copper_outbox",
        sa.Column("redrive_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("copper_outbox", "redrive_count")
