"""
Tests for the issue #249 Alembic migration
(alembic/versions/y3z4a5b6c7d8_drop_today_line_and_partner_name.py), which
rewrites existing `email_templates` rows to drop the "today" sentence and
sign off as "Raed Ventures team" / "فريق رائد فنتشرز" instead of
{partner_name} -- but only a row whose body still matches the byte-for-byte
text seeded by c5d6e7f8a9b0, so a row an admin already hand-edited via the
Email templates screen (issue #241) survives untouched.

Loaded by file path (not a package import) since alembic/versions has no
__init__.py, mirroring test_migration_lead_source_backfill.py. Runs upgrade()
/downgrade() against a real (in-memory SQLite) connection, standing in for
`op.get_bind()`, since there's no live Postgres in this CI.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

import sqlalchemy as sa

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "y3z4a5b6c7d8_drop_today_line_and_partner_name.py"
)

_spec = importlib.util.spec_from_file_location("drop_today_line_migration", MIGRATION_PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _make_table_and_engine():
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    table = sa.Table(
        "email_templates",
        meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("language", sa.String),
        sa.Column("body", sa.Text),
    )
    meta.create_all(engine)
    return table, engine


def _body_by_id(conn, table):
    rows = conn.execute(sa.select(table.c.id, table.c.body)).fetchall()
    return {row.id: row.body for row in rows}


def test_upgrade_rewrites_seeded_english_row():
    table, engine = _make_table_and_engine()
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"id": "1", "language": "en", "body": migration._OLD_EN_BODY}])
        migration.op.get_bind = lambda: conn
        migration.upgrade()
        body = _body_by_id(conn, table)["1"]
    assert body == migration._NEW_EN_BODY
    assert "This reflects where Raed Ventures is today" not in body
    assert "{partner_name}" not in body
    assert body.endswith("Raed Ventures team")


def test_upgrade_rewrites_seeded_arabic_row():
    table, engine = _make_table_and_engine()
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"id": "1", "language": "ar", "body": migration._OLD_AR_BODY}])
        migration.op.get_bind = lambda: conn
        migration.upgrade()
        body = _body_by_id(conn, table)["1"]
    assert body == migration._NEW_AR_BODY
    assert "يعكس وضع رائد فنتشرز" not in body
    assert body.endswith("فريق رائد فنتشرز")


def test_upgrade_leaves_hand_edited_row_untouched():
    table, engine = _make_table_and_engine()
    hand_edited = "A completely custom body an admin wrote in the Email templates screen."
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"id": "1", "language": "en", "body": hand_edited}])
        migration.op.get_bind = lambda: conn
        migration.upgrade()
        body = _body_by_id(conn, table)["1"]
    assert body == hand_edited


def test_upgrade_only_touches_rows_matching_its_own_language():
    table, engine = _make_table_and_engine()
    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {"id": "en-row", "language": "en", "body": migration._OLD_EN_BODY},
                {"id": "ar-row", "language": "ar", "body": migration._OLD_AR_BODY},
            ],
        )
        migration.op.get_bind = lambda: conn
        migration.upgrade()
        bodies = _body_by_id(conn, table)
    assert bodies["en-row"] == migration._NEW_EN_BODY
    assert bodies["ar-row"] == migration._NEW_AR_BODY


def test_downgrade_restores_seeded_rows_and_leaves_hand_edited_row_alone():
    table, engine = _make_table_and_engine()
    hand_edited = "A completely custom body an admin wrote in the Email templates screen."
    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            [
                {"id": "1", "language": "en", "body": migration._OLD_EN_BODY},
                {"id": "2", "language": "en", "body": hand_edited},
                {"id": "3", "language": "ar", "body": migration._OLD_AR_BODY},
            ],
        )
        migration.op.get_bind = lambda: conn
        migration.upgrade()
        migration.downgrade()
        bodies = _body_by_id(conn, table)
    assert bodies["1"] == migration._OLD_EN_BODY
    assert bodies["2"] == hand_edited
    assert bodies["3"] == migration._OLD_AR_BODY
