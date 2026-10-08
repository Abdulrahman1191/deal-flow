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


def test_no_missing_parents():
    script_dir = _script_directory()
    known_ids = set(_revision_ids())
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        revision = script_dir.get_revision(
            REVISION_RE.search(path.read_text()).group(1)
        )
        if revision.down_revision is None:
            continue
        down_revisions = (
            revision.down_revision
            if isinstance(revision.down_revision, tuple)
            else (revision.down_revision,)
        )
        for parent in down_revisions:
            assert parent in known_ids, (
                f"{path.name} declares down_revision {parent!r}, which no "
                "file on disk provides. A database already stamped at that "
                "revision would fail to resolve it on `alembic upgrade head`."
            )


def test_e8f9a0b1c2d3_not_deleted_again():
    """Regression guard for issue #230.

    #229 deleted e8f9a0b1c2d3 after production had already applied it
    (51 seconds before #229's own collision was introduced), which left prod
    stamped at a revision no longer resolvable and 502'd the app. Keep the
    file on disk and chained as a3b4c5d6e7f8's direct parent so a database
    stamped at either revision can always reach head.
    """
    ids = set(_revision_ids())
    assert "e8f9a0b1c2d3" in ids, (
        "e8f9a0b1c2d3_add_rejection_reasons.py must stay on disk -- it was "
        "already applied in production. Deleting it breaks `alembic upgrade "
        "head` for any database stamped at that revision."
    )

    script_dir = _script_directory()
    a3b4 = script_dir.get_revision("a3b4c5d6e7f8")
    assert a3b4.down_revision == "e8f9a0b1c2d3", (
        "a3b4c5d6e7f8's down_revision must be e8f9a0b1c2d3 so the two "
        "rejection_reasons migrations form one linear chain instead of two "
        "branches off the same parent."
    )
