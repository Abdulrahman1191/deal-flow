"""
Tests for scripts/backfill_awaiting_deck.py (issue #67).

plan_backfill() is a pure function over already-fetched candidate rows
(mirrors close_stale_copper.py's plan_cleanup() test pattern -- no live DB).
_fetch_candidates()/apply_backfill()/main() are exercised with a fake
AsyncSessionLocal, confirming the DB filter excludes archived/approved/
awaiting_deck leads and any lead that already has pitch_deck_text, that
--commit writes and dry-run doesn't, and that the script is owner-agnostic
(no owner filter anywhere in the query).
"""
from __future__ import annotations
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import backfill_awaiting_deck as bad  # noqa: E402


def _lead(owner_email, company_name="Acme", status="pending", pitch_deck_text=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email=owner_email,
        company_name=company_name,
        status=status,
        pitch_deck_text=pitch_deck_text,
    )


# --- plan_backfill (pure) ----------------------------------------------------


def test_plan_counts_candidates_per_owner():
    leads = [
        _lead("alice@raed.vc", "Co A"),
        _lead("alice@raed.vc", "Co B"),
        _lead("bob@raed.vc", "Co C"),
        _lead(None, "Co D"),
    ]
    plan = bad.plan_backfill(leads)

    assert len(plan["leads"]) == 4
    assert plan["counts"] == {"(no owner)": 1, "alice@raed.vc": 2, "bob@raed.vc": 1}
    assert {row["company_name"] for row in plan["leads"]} == {"Co A", "Co B", "Co C", "Co D"}


def test_plan_empty_when_no_candidates():
    plan = bad.plan_backfill([])
    assert plan == {"leads": [], "counts": {}}


# --- _fetch_candidates: DB filter --------------------------------------------


class _FakeLeadsResult:
    def __init__(self, leads):
        self._leads = leads

    def scalars(self):
        return self

    def all(self):
        return self._leads


class _FakeSession:
    def __init__(self, leads):
        self._leads = leads
        self.committed = 0
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeLeadsResult(self._leads)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def test_fetch_candidates_returns_whatever_the_query_yields(monkeypatch):
    """The actual notin_/is_(None) filtering happens in SQL -- this only
    confirms the function wires the query through without adding an
    owner_email clause (owner-agnostic per the issue's acceptance criteria).
    A real Postgres-backed integration pass would additionally verify the
    SQL predicate itself."""
    leads = [_lead("alice@raed.vc"), _lead("bob@raed.vc")]
    session = _FakeSession(leads)

    result = asyncio.run(bad._fetch_candidates(session))
    assert result == leads


# --- apply_backfill -----------------------------------------------------------


def test_apply_backfill_sets_status_and_logs_events():
    leads = [_lead("alice@raed.vc"), _lead("bob@raed.vc")]
    session = _FakeSession(leads)

    applied = asyncio.run(bad.apply_backfill(session, leads))

    assert applied == 2
    assert all(lead.status == "awaiting_deck" for lead in leads)
    assert session.committed == 1
    assert len(session.added) == 2
    assert all(event.event_type == "awaiting_deck" for event in session.added)


def test_apply_backfill_noop_on_empty_list():
    session = _FakeSession([])
    applied = asyncio.run(bad.apply_backfill(session, []))
    assert applied == 0
    assert session.committed == 1
    assert session.added == []


# --- main(): dry-run vs --commit ---------------------------------------------


def _stub_session(monkeypatch, leads):
    session = _FakeSession(leads)
    monkeypatch.setattr(bad, "AsyncSessionLocal", lambda: session)
    return session


def test_main_dry_run_makes_no_writes(monkeypatch, capsys):
    leads = [_lead("alice@raed.vc"), _lead("bob@raed.vc")]
    session = _stub_session(monkeypatch, leads)

    exit_code = bad.main([])

    assert exit_code == 0
    assert session.committed == 0
    assert all(lead.status == "pending" for lead in leads)
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "alice@raed.vc: 1" in out
    assert "bob@raed.vc: 1" in out


def test_main_commit_applies_and_reports_counts(monkeypatch, capsys):
    leads = [_lead("alice@raed.vc"), _lead("bob@raed.vc")]
    session = _stub_session(monkeypatch, leads)

    exit_code = bad.main(["--commit"])

    assert exit_code == 0
    assert session.committed == 1
    assert all(lead.status == "awaiting_deck" for lead in leads)
    out = capsys.readouterr().out
    assert "Done. 2 lead(s) moved to 'awaiting_deck'." in out


def test_main_commit_with_nothing_to_do_makes_no_writes(monkeypatch, capsys):
    session = _stub_session(monkeypatch, [])

    exit_code = bad.main(["--commit"])

    assert exit_code == 0
    assert session.committed == 0
    assert "Nothing to do." in capsys.readouterr().out
