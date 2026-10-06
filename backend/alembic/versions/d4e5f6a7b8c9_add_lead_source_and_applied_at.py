"""add leads.source/source_channel/source_detail/applied_at + backfill

Issue #217: `source` existed only as free text buried in
raw_copper_data["custom_fields"], and `applied_at` was computed at
serialization time from raw_copper_data["date_created"] -- neither was
queryable or indexable in SQL, so the board couldn't filter on either.

This adds the four columns and backfills every existing row by re-deriving
from its already-stored `raw_copper_data` -- no Copper API calls. Rows with
no `raw_copper_data` are left exactly as the column defaults set them
(source='unknown', applied_at=NULL -- LeadOut.applied_at still falls back to
created_at for those at serialization time).

Derivation mirrors app/services/copper_service.derive_lead_source /
derive_applied_at as of this revision. It's duplicated here rather than
imported so this migration's behaviour stays fixed even if that code changes
later.

Revision ID: d4e5f6a7b8c9
Revises: w1a2b3c4d5e6
Create Date: 2026-10-06
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "w1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors app/services/copper_service.py's and app/config.py's defaults as of
# this revision.
SOURCE_DETAIL_FIELD_ID = 244394
APPLICATION_INBOX_EMAIL = "info@raed.vc"
CUSTOMER_SOURCE_ID_WEBSITE_FORM = 1444487

leads_table = sa.table(
    "leads",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("raw_copper_data", postgresql.JSONB),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("source", sa.String(32)),
    sa.column("source_channel", sa.String(64)),
    sa.column("source_detail", sa.Text),
    sa.column("applied_at", sa.DateTime(timezone=True)),
)


def _get_custom_field_value(raw_copper_data: Optional[dict], field_id: int) -> str:
    if not field_id or not raw_copper_data:
        return ""
    for cf in raw_copper_data.get("custom_fields") or []:
        if cf.get("custom_field_definition_id") == field_id:
            value = cf.get("value")
            return value.strip() if isinstance(value, str) and value.strip() else ""
    return ""


def _source_channel_from_text(text: str) -> Optional[str]:
    token = re.split(r"[\/\s]+", text.strip(), maxsplit=1)[0].strip().lower()
    return token or None


def _classify_source(raw_copper_data: Optional[dict]) -> dict:
    detail = _get_custom_field_value(raw_copper_data, SOURCE_DETAIL_FIELD_ID)
    if detail:
        lowered = detail.lower()
        if APPLICATION_INBOX_EMAIL in lowered or lowered.startswith("emailed"):
            return {"source": "email_inbox", "source_channel": None, "source_detail": detail}
        if "website" in lowered and "submission" in lowered:
            _, _, after_colon = detail.partition(":")
            return {
                "source": "website_form",
                "source_channel": _source_channel_from_text(after_colon),
                "source_detail": detail,
            }
        return {"source": "other", "source_channel": None, "source_detail": detail}

    customer_source_id = (raw_copper_data or {}).get("customer_source_id")
    if customer_source_id and int(customer_source_id) == CUSTOMER_SOURCE_ID_WEBSITE_FORM:
        return {"source": "website_form", "source_channel": None, "source_detail": None}
    return {"source": "unknown", "source_channel": None, "source_detail": None}


def _resolve_applied_at(raw_copper_data: Optional[dict], created_at: datetime) -> datetime:
    if isinstance(raw_copper_data, dict):
        date_created = raw_copper_data.get("date_created")
        if isinstance(date_created, (int, float)) and not isinstance(date_created, bool):
            try:
                return datetime.fromtimestamp(date_created, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                pass
    return created_at


def _backfill(connection) -> None:
    rows = connection.execute(
        sa.select(leads_table.c.id, leads_table.c.raw_copper_data, leads_table.c.created_at)
        .where(leads_table.c.raw_copper_data.is_not(None))
    ).fetchall()
    for row in rows:
        classified = _classify_source(row.raw_copper_data)
        connection.execute(
            leads_table.update()
            .where(leads_table.c.id == row.id)
            .values(applied_at=_resolve_applied_at(row.raw_copper_data, row.created_at), **classified)
        )


def upgrade() -> None:
    op.add_column("leads", sa.Column("source", sa.String(32), nullable=False, server_default="unknown"))
    op.add_column("leads", sa.Column("source_channel", sa.String(64), nullable=True))
    op.add_column("leads", sa.Column("source_detail", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_leads_source", "leads", ["source"])
    op.create_index("ix_leads_applied_at", "leads", ["applied_at"])

    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_leads_applied_at", table_name="leads")
    op.drop_index("ix_leads_source", table_name="leads")
    op.drop_column("leads", "applied_at")
    op.drop_column("leads", "source_detail")
    op.drop_column("leads", "source_channel")
    op.drop_column("leads", "source")
