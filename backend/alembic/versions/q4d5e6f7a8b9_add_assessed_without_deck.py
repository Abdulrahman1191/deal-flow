"""add assessment_cards.assessed_without_deck

Deckless leads with a usable website or substantial description now get
scored instead of parked in awaiting_deck (issue #144). This column flags
whether a given score was made without a pitch deck -- website/description
only -- so partners can see it's lower-confidence and know a deck arriving
later would refine it.

Revision ID: q4d5e6f7a8b9
Revises: p3c4d5e6f7a8
Create Date: 2026-08-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q4d5e6f7a8b9"
down_revision: Union[str, None] = "p3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_cards",
        sa.Column("assessed_without_deck", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "assessed_without_deck")
