"""add company_linkedin_url and user_override_at

Revision ID: f5a374b41302
Revises: 0001
Create Date: 2026-05-13 08:40:27.912849

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a374b41302'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("company_linkedin_url", sa.String(length=512), nullable=True))
    op.add_column(
        "assessment_cards",
        sa.Column("user_override_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_cards", "user_override_at")
    op.drop_column("leads", "company_linkedin_url")
