"""add leads.deck_wait_started_at

A brand-new deck-less lead now waits in awaiting_deck for a grace period
instead of getting an immediate deck-less verdict (issue #149). This column
records when that wait started, so promote_awaiting_deck.py's periodic
fallback knows when the grace period (settings.deck_grace_period_days)
elapses. Nullable -- rows that enter awaiting_deck some other way fall back
to created_at (see the promote task's query).

Revision ID: r5e6f7a8b9c0
Revises: q4d5e6f7a8b9
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r5e6f7a8b9c0"
down_revision: Union[str, None] = "q4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("deck_wait_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leads", "deck_wait_started_at")
