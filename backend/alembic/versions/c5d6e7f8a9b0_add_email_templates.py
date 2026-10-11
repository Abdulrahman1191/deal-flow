"""add email_templates and email_template_versions tables

Moves the four rejection email templates (issue #232: CONFLICT/MARKET_SIZE/
TRACTION/MANDATE x en/ar) out of Python constants into the database, so copy
can change without a deploy -- and so a change reaches every lead, including
ones assessed months ago, with no backfill (issue #234).

`email_templates` is keyed by (language, template_key). Seeded here byte for
byte from app.services.rejection_templates -- the same constants
app.services.email_templates.render_rejection_email() falls back to when a
row is missing -- including the Arabic diacritics (حكمًا, تمكّن). Behaviour
on the first boot after this ships must be identical to behaviour before
it: do not normalise or edit those strings when touching this file.

`email_template_versions` holds one row per prior save (and, since a revert
snapshots the row it's about to overwrite, one row per revert too) so any
earlier subject/body/reason_text combination can be restored as a unit.

Reversible: downgrade drops both tables. Nothing here touches
assessment_cards -- no rendered text is ever stamped onto a card by this
migration or by the render path, and no lead is re-assessed.

Revision ID: c5d6e7f8a9b0
Revises: a3b4c5d6e7f8
Create Date: 2026-10-11
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("template_key", sa.String(32), nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("reason_text", sa.Text, nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("language", "template_key", name="uq_email_templates_language_template_key"),
    )
    op.create_table(
        "email_template_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("template_key", sa.String(32), nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("reason_text", sa.Text, nullable=False),
        sa.Column("saved_by", sa.String(255), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_email_template_versions_template_id", "email_template_versions", ["template_id"])

    from app.services.email_templates import seed_rows

    email_templates_table = sa.table(
        "email_templates",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("language", sa.String),
        sa.column("template_key", sa.String),
        sa.column("subject", sa.Text),
        sa.column("body", sa.Text),
        sa.column("reason_text", sa.Text),
    )
    op.bulk_insert(
        email_templates_table,
        [
            {
                "id": uuid.uuid4(),
                "language": row["language"],
                "template_key": row["template_key"],
                "subject": row["subject"],
                "body": row["body"],
                "reason_text": row["reason_text"],
            }
            for row in seed_rows()
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_email_template_versions_template_id", table_name="email_template_versions")
    op.drop_table("email_template_versions")
    op.drop_table("email_templates")
