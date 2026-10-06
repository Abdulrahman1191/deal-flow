"""
Tests for the issue #217 Alembic migration
(alembic/versions/d4e5f6a7b8c9_add_lead_source_and_applied_at.py), which adds
`Lead.source`/`source_channel`/`source_detail`/`applied_at` and backfills
existing rows by re-deriving from each row's already-stored
`raw_copper_data` -- no Copper API calls.

Loaded by file path (not a package import) since alembic/versions has no
__init__.py, mirroring how Alembic itself loads revision files.
"""
from __future__ import annotations
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "d4e5f6a7b8c9_add_lead_source_and_applied_at.py"
)

_spec = importlib.util.spec_from_file_location("lead_source_backfill_migration", MIGRATION_PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


SOURCE_DETAIL_FIELD_ID = migration.SOURCE_DETAIL_FIELD_ID


# ---------- pure classification helpers (duplicated from copper_service on purpose) ----------


def test_classify_source_emailed_is_email_inbox():
    raw = {"custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": "Emailed info@raed.vc: Hi"}]}
    assert migration._classify_source(raw) == {
        "source": "email_inbox",
        "source_channel": None,
        "source_detail": "Emailed info@raed.vc: Hi",
    }


def test_classify_source_website_submission_with_channel():
    raw = {"custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": "Website (EN) submission: LinkedIn"}]}
    result = migration._classify_source(raw)
    assert result["source"] == "website_form"
    assert result["source_channel"] == "linkedin"


def test_classify_source_unrecognised_is_other():
    raw = {"custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": "Referred by a friend"}]}
    assert migration._classify_source(raw)["source"] == "other"


def test_classify_source_blank_detail_falls_back_to_customer_source_id():
    assert migration._classify_source({"customer_source_id": 1444487})["source"] == "website_form"
    assert migration._classify_source({})["source"] == "unknown"
    assert migration._classify_source(None)["source"] == "unknown"


def test_resolve_applied_at_prefers_date_created():
    epoch = 1750000000
    created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert migration._resolve_applied_at({"date_created": epoch}, created_at) == datetime.fromtimestamp(
        epoch, tz=timezone.utc
    )


def test_resolve_applied_at_falls_back_to_created_at():
    created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert migration._resolve_applied_at({}, created_at) == created_at
    assert migration._resolve_applied_at(None, created_at) == created_at
    assert migration._resolve_applied_at({"date_created": "garbage"}, created_at) == created_at


# ---------- _backfill: DB wiring ----------


class _FakeSelectResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeRow:
    def __init__(self, id, raw_copper_data, created_at):
        self.id = id
        self.raw_copper_data = raw_copper_data
        self.created_at = created_at


class _FakeConnection:
    """Records every statement's literal-bound SQL text instead of actually
    executing anything -- lets the test assert both the SELECT's filter and
    each UPDATE's values without a live Postgres connection."""

    def __init__(self, rows):
        self._rows = rows
        self.statements: list[str] = []

    def execute(self, query):
        text = str(query.compile(compile_kwargs={"literal_binds": True}))
        self.statements.append(text)
        if text.strip().upper().startswith("SELECT"):
            return _FakeSelectResult(self._rows)
        return None


def test_backfill_selects_only_rows_with_raw_copper_data():
    connection = _FakeConnection([])
    migration._backfill(connection)

    select_stmt = connection.statements[0]
    assert "SELECT" in select_stmt.upper()
    assert "IS NOT NULL" in select_stmt.upper()
    # No rows returned -> no UPDATE issued at all.
    assert len(connection.statements) == 1


def test_backfill_populates_source_and_applied_at_from_raw_copper_data():
    created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    epoch = 1750000000
    row_id = uuid.uuid4()
    raw = {
        "custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": "Emailed info@raed.vc: Hi"}],
        "date_created": epoch,
    }
    connection = _FakeConnection([_FakeRow(row_id, raw, created_at)])

    migration._backfill(connection)

    update_stmts = connection.statements[1:]
    assert len(update_stmts) == 1
    stmt = update_stmts[0]
    # The generic literal processor for a UUID column renders it without
    # dashes.
    assert row_id.hex in stmt
    assert "email_inbox" in stmt
    assert "Emailed info@raed.vc: Hi" in stmt
    # applied_at resolves from date_created (epoch seconds), not created_at --
    # the literal-bound minute/seconds of the derived timestamp must appear.
    assert datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") in stmt


def test_backfill_leaves_rows_without_raw_copper_data_untouched():
    """The SELECT's own WHERE excludes raw_copper_data IS NULL rows, so no
    UPDATE is ever issued for them -- this is the "leaves rows without it
    untouched" acceptance criterion."""
    connection = _FakeConnection([])  # nothing matches the IS NOT NULL filter

    migration._backfill(connection)

    assert len(connection.statements) == 1  # the SELECT only, no UPDATEs
