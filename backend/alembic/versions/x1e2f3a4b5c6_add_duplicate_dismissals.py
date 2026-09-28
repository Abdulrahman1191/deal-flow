"""add duplicate_dismissals table

Issue #180: owner-only duplicate-lead flagging surface. A dismissal is keyed
on the exact sorted set of lead ids in a cluster (cluster_key) so a cluster
that later gains a new member gets a different key and reappears for review
instead of staying silently dismissed forever.

Revision ID: x1e2f3a4b5c6
Revises: w0d1e2f3a4b5
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "x1e2f3a4b5c6"
down_revision: Union[str, None] = "w0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "duplicate_dismissals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_key", sa.String(length=2048), nullable=False),
        sa.Column("lead_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("dismissed_by", sa.String(255), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_duplicate_dismissals_cluster_key", "duplicate_dismissals", ["cluster_key"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_duplicate_dismissals_cluster_key", table_name="duplicate_dismissals")
    op.drop_table("duplicate_dismissals")
