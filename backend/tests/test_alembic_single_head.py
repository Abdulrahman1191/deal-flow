"""
Regression guard for issue #228.

#223's a3b4c5d6e7f8 and #225's e8f9a0b1c2d3 both branched from the same
down_revision (c5e89de040a4) and merged a minute apart, leaving two Alembic
heads that made `alembic upgrade head` abort on container boot -- production
502'd until the duplicate migration was deleted. Nothing in CI checked the
revision graph, so the collision only surfaced in production.

These tests load the real migration directory (no DB needed) and assert the
graph stays a single line: exactly one head, and no two files claiming the
same revision id.
"""
from __future__ import annotations

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parent.parent
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"

REVISION_RE = re.compile(r'^revision:.*=\s*["\']([0-9a-zA-Z]+)["\']', re.MULTILINE)


def _script_directory() -> ScriptDirectory:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config)


def _revision_ids() -> list[str]:
    ids = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        match = REVISION_RE.search(path.read_text())
        assert match, f"{path.name} has no `revision = ...` assignment"
        ids.append(match.group(1))
    return ids


def test_no_duplicate_revision_ids():
    ids = _revision_ids()
    duplicates = {rid for rid in ids if ids.count(rid) > 1}
    assert not duplicates, (
        f"Duplicate Alembic revision ids across versions/: {duplicates}. "
        "Two migration files are claiming the same revision -- rename one."
    )


def test_exactly_one_head():
    heads = _script_directory().get_heads()
    assert len(heads) == 1, (
        f"Expected exactly one Alembic head, found {len(heads)}: {heads}. "
        "Two migrations likely branched from the same down_revision -- chain "
        "one after the other instead of adding an `alembic merge` revision "
        "(a merge would run both and duplicate any overlapping add_column)."
    )
