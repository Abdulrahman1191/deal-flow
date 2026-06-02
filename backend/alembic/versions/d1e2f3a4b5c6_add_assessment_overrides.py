"""add assessment_overrides table for LLM training-data capture

Revision ID: d1e2f3a4b5c6
Revises: c0a1b2c3d4e5
Create Date: 2026-05-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Persist research input alongside the AI's output so override capture
    # can snapshot the full (input, output) pair for future training.
    op.add_column(
        "assessment_cards",
        sa.Column("research_data", postgresql.JSONB, nullable=True),
    )

    op.create_table(
        "assessment_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_bucket", sa.String(8), nullable=False),
        sa.Column("ai_confidence", sa.Integer),
        sa.Column("ai_summary", sa.Text),
        sa.Column("ai_breakdown", postgresql.JSONB),
        sa.Column("human_bucket", sa.String(8), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),  # override | approve | skip | send
        sa.Column("research_snap", postgresql.JSONB),
        sa.Column("deck_excerpt", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_overrides_created_at", "assessment_overrides", ["created_at"])
    op.create_index("ix_overrides_lead_id", "assessment_overrides", ["lead_id"])


def downgrade() -> None:
    op.drop_index("ix_overrides_lead_id", table_name="assessment_overrides")
    op.drop_index("ix_overrides_created_at", table_name="assessment_overrides")
    op.drop_table("assessment_overrides")
    op.drop_column("assessment_cards", "research_data")
