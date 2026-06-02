"""unique assessment card per lead

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_assessment_cards_lead_id",
        "assessment_cards",
        ["lead_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_assessment_cards_lead_id", "assessment_cards", type_="unique")
