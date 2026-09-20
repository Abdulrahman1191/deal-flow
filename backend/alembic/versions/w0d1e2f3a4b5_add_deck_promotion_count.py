"""add leads.deck_promotion_count

Bounds the awaiting_deck re-park loop (issue #170): promote_awaiting_deck.py
previously reset deck_wait_started_at on every promotion with no counter, so
a deck-less lead with genuinely no usable context (no deck, no website,
blank description) got a fresh grace period every cycle and looped through
awaiting_deck forever with no signal to the partner. This column is
incremented on each promotion; once it exceeds settings.max_deck_promotions,
assess_lead._run stops re-parking and writes a MAYBE placeholder card
instead. Reset to 0 on the next successful assessment.

Revision ID: w0d1e2f3a4b5
Revises: v9c0d1e2f3a4
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w0d1e2f3a4b5"
down_revision: Union[str, None] = "v9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("deck_promotion_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("leads", "deck_promotion_count")
