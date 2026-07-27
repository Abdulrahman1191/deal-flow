"""add users.calendly_url

Per-user Calendly booking link so generated outreach drafts use the lead
owner's own link + name instead of a single hardcoded one (issue #84).
Nullable -- backend/scripts/seed_calendly_links.py seeds the known links;
draft generation falls back to the previous default when unset.

Revision ID: m0a1b2c3d4e5
Revises: l9f0a1b2c3d4
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m0a1b2c3d4e5"
down_revision: Union[str, None] = "l9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("calendly_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "calendly_url")
