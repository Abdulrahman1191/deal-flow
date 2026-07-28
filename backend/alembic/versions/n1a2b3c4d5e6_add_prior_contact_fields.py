"""add leads.prior_contact fields

Detect prior email communication with a lead from Copper's activity feed
(issue #90). Nullable -- left null until the first sync successfully
computes a value, and stays null across an activities-fetch failure
(best-effort, per-lead try/except in sync_copper.py).

prior_contact_checked_at is internal cache bookkeeping (drives the
refresh-window in sync_copper.py) and isn't exposed via the API.

Revision ID: n1a2b3c4d5e6
Revises: m0a1b2c3d4e5
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n1a2b3c4d5e6"
down_revision: Union[str, None] = "m0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("prior_contact", sa.Boolean(), nullable=True))
    op.add_column("leads", sa.Column("prior_contact_count", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("prior_contact_last_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("prior_contact_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "prior_contact_checked_at")
    op.drop_column("leads", "prior_contact_last_at")
    op.drop_column("leads", "prior_contact_count")
    op.drop_column("leads", "prior_contact")
