"""add lead_action_log + assessment_overrides.reverted_at

Undo mechanism (issue #153, narrowed to archive-only for this PR):
`lead_action_log` snapshots the prior lead.status + Copper tag set right
before an archive action overwrites them, so POST /leads/{id}/undo can
restore it exactly. `assessment_overrides.reverted_at` marks the training
row an undone action produced so it's excluded from
feedback_patterns.retrieve_labeled_exemplars -- an accidental archive must
never permanently teach the model "the team rejects companies like this".

Revision ID: u8b9c0d1e2f3
Revises: t7a8b9c0d1e2
Create Date: 2026-08-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "u8b9c0d1e2f3"
down_revision: Union[str, None] = "t7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lead_action_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("actor_email", sa.String(255)),
        sa.Column("prior_state", postgresql.JSONB, nullable=False),
        sa.Column("email_sent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("copper_outbox_id", postgresql.UUID(as_uuid=True)),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_lead_action_log_lead_id", "lead_action_log", ["lead_id"])

    op.add_column(
        "assessment_overrides",
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_overrides", "reverted_at")
    op.drop_index("ix_lead_action_log_lead_id", table_name="lead_action_log")
    op.drop_table("lead_action_log")
