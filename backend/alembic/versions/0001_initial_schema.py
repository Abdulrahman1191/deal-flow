"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("copper_id", sa.String(64), unique=True, nullable=True),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("website", sa.String(512)),
        sa.Column("description", sa.Text),
        sa.Column("stage", sa.String(64)),
        sa.Column("region", sa.String(128)),
        sa.Column("founder_names", postgresql.ARRAY(sa.Text)),
        sa.Column("linkedin_urls", postgresql.ARRAY(sa.Text)),
        sa.Column("pitch_deck_s3", sa.String(512)),
        sa.Column("raw_copper_data", postgresql.JSONB),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])

    op.create_table(
        "assessment_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bucket", sa.String(16), nullable=False),
        sa.Column("confidence_score", sa.Integer, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("positive_signals", postgresql.JSONB),
        sa.Column("red_flags", postgresql.JSONB),
        sa.Column("data_gaps", postgresql.JSONB),
        sa.Column("scoring_breakdown", postgresql.JSONB),
        sa.Column("draft_subject", sa.Text),
        sa.Column("draft_body", sa.Text),
        sa.Column("draft_type", sa.String(16)),
        sa.Column("research_sources", postgresql.JSONB),
        sa.Column("user_override", sa.String(16)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_assessment_cards_lead_id", "assessment_cards", ["lead_id"])
    op.create_index("ix_assessment_cards_bucket", "assessment_cards", ["bucket"])

    op.create_table(
        "daily_briefings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("date", sa.Date, nullable=False, unique=True),
        sa.Column("top_themes", postgresql.JSONB, nullable=False),
        sa.Column("deep_dives", postgresql.JSONB, nullable=False),
        sa.Column("raw_research", sa.Text),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_daily_briefings_date", "daily_briefings", ["date"])


def downgrade() -> None:
    op.drop_table("daily_briefings")
    op.drop_table("assessment_cards")
    op.drop_table("leads")
    op.drop_table("users")
