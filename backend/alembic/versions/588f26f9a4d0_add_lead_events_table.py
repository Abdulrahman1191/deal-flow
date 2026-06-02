"""add lead_events table

Revision ID: 588f26f9a4d0
Revises: 833d90a0b5c3
Create Date: 2026-05-13 09:19:06.243752

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '588f26f9a4d0'
down_revision: Union[str, None] = '833d90a0b5c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lead_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_lead_events_lead_id", "lead_events", ["lead_id"])
    op.create_index(
        "ix_lead_events_created_at",
        "lead_events",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_lead_events_created_at", table_name="lead_events")
    op.drop_index("ix_lead_events_lead_id", table_name="lead_events")
    op.drop_table("lead_events")
