"""add llm_usage table

Records what every DeepSeek call costs (issue #191): prompt/completion/
total tokens, model, duration, and outcome (ok / error / breaker_open),
attributed by `purpose` and an optional `lead_id`. Written best-effort from
app.services.claude_agent._chat_completion, the single choke point every
DeepSeek call goes through. Indexed on created_at and purpose so
GET /api/v1/ops/llm-usage can compute windowed totals and a per-purpose
breakdown without a table scan.

Revision ID: z2a3b4c5d6e7
Revises: y1a2b3c4d5e6
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "z2a3b4c5d6e7"
down_revision: Union[str, None] = "y1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_llm_usage_purpose", "llm_usage", ["purpose"])
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_index("ix_llm_usage_purpose", table_name="llm_usage")
    op.drop_table("llm_usage")
