"""add copper opportunity foreign keys

Revision ID: 833d90a0b5c3
Revises: f5a374b41302
Create Date: 2026-05-13 08:59:40.798998

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '833d90a0b5c3'
down_revision: Union[str, None] = 'f5a374b41302'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("copper_person_id", sa.String(length=64), nullable=True))
    op.add_column("leads", sa.Column("copper_company_id", sa.String(length=64), nullable=True))
    op.add_column("leads", sa.Column("copper_opportunity_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "copper_opportunity_id")
    op.drop_column("leads", "copper_company_id")
    op.drop_column("leads", "copper_person_id")
