"""add users.onboarded_at (first-run onboarding flag)

Existing users are backfilled as already-onboarded so they don't see the
welcome flow; brand-new users get NULL and are onboarded on first login.

Revision ID: k8e9f0a1b2c3
Revises: j7d8e9f0a1b2
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k8e9f0a1b2c3"
down_revision: Union[str, None] = "j7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET onboarded_at = now() WHERE onboarded_at IS NULL")


def downgrade() -> None:
    op.drop_column("users", "onboarded_at")
