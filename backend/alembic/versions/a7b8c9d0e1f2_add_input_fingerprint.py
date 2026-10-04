"""add input_fingerprint to assessment_cards

sha256 of every input that fed the assessor's prompt when a card was written
-- company name, description, pitch deck text, and (deck-less leads only)
scraped website content (issue #203). The assessor runs at temperature=0, so
an unchanged fingerprint guarantees a re-run would return the identical
verdict -- POST /leads/bulk-reassess uses this to skip leads with nothing
new to re-score instead of silently burning ~12k input tokens per lead for
the same bucket.

Revision ID: a7b8c9d0e1f2
Revises: z2a3b4c5d6e7
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "z2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assessment_cards", sa.Column("input_fingerprint", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("assessment_cards", "input_fingerprint")
