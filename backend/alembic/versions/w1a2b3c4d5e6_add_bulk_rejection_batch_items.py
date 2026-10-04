"""add bulk_rejection_batch_items

Issue #205: POST /leads/bulk-send-rejection sends go out through per-lead,
throttled Celery tasks dispatched after the request returns, so (unlike
bulk-archive/bulk-reassign) the request can't synchronously report
sent/failed/skipped counts. This table lets the task record each lead's
outcome as it resolves, so GET /leads/bulk-send-rejection/{batch_id} can
report a partial batch instead of guessing.

Revision ID: w1a2b3c4d5e6
Revises: 4d8c27bc9ea7
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "w1a2b3c4d5e6"
down_revision: Union[str, None] = "4d8c27bc9ea7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bulk_rejection_batch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_email", sa.String(255), nullable=False),
        sa.Column("company_name", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_bulk_rejection_batch_items_batch_id", "bulk_rejection_batch_items", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_bulk_rejection_batch_items_batch_id", table_name="bulk_rejection_batch_items")
    op.drop_table("bulk_rejection_batch_items")
