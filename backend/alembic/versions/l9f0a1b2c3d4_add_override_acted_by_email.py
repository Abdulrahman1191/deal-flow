"""add assessment_overrides.acted_by_email

Records which user performed the rate/override/approve/skip action, so
per-user access-control tests can assert on it. Existing rows predate the
column and are left NULL.

Revision ID: l9f0a1b2c3d4
Revises: k8e9f0a1b2c3
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "l9f0a1b2c3d4"
down_revision: Union[str, None] = "k8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assessment_overrides", sa.Column("acted_by_email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("assessment_overrides", "acted_by_email")
