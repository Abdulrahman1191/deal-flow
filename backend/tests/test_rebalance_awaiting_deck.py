"""
Tests for scripts/rebalance_awaiting_deck.py (issue #170) -- the one-off
backfill that applies the new email-sourced-skip/shortened-grace/promotion-
cap rules to leads already parked in `awaiting_deck` under the old (5-day
grace, no cap) behaviour.

plan_rebalance() is a pure function over already-fetched rows (mirrors
close_stale_copper.py's plan_cleanup() / backfill_awaiting_deck.py's
plan_backfill() test pattern -- no live DB). _fetch_rows()/apply_rebalance()/
main() are exercised with a fake AsyncSessionLocal session.
"""
from __future__ import annotations
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from app.config import settings

import rebalance_awaiting_deck as rad  # noqa: E402


def _source_detail_raw(value, field_id=None):
    return {
        "custom_fields": [
            {"custom_field_definition_id": field_id or settings.copper_cf_source_detail_id, "value": value}
        ]
    }


def _lead(company_name="Acme", owner_email="alice@raed.vc", pitch_deck_text=None,
          website=None, description=None, raw_copper_data=None,
          deck_wait_started_at=None, created_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        company_name=company_name,
        owner_email=owner_email,
        status="awaiting_deck",
        pitch_deck_text=pitch_deck_text,
        website=website,
        description=description,
        raw_copper_data=raw_copper_data,
        deck_wait_started_at=deck_wait_started_at,
        created_at=created_at or datetime.now(timezone.utc),
    )


# --- plan_rebalance (pure) ----------------------------------------------------

def test_plan_queues_email_sourced_lead_even_within_grace(monkeypatch):
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    lead = _lead(raw_copper_data=_source_detail_raw("Emailed info@raed.vc: a subject"))

    plan = rad.plan_rebalance([{"lead": lead, "park_count": 1}])

    assert plan["counts"] == {"queue": 1, "maybe_placeholder": 0, "none": 0}
    assert plan["queue"][0]["email_sourced"] is True


def test_plan_queues_lead_past_grace(monkeypatch):
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    old = datetime.now(timezone.utc) - timedelta(days=5)
    lead = _lead(created_at=old, deck_wait_started_at=old)

    plan = rad.plan_rebalance([{"lead": lead, "park_count": 1}])

    assert plan["counts"] == {"queue": 1, "maybe_placeholder": 0, "none": 0}
    assert plan["queue"][0]["past_grace"] is True


def test_plan_maybe_placeholders_lead_past_cap_with_no_local_context(monkeypatch):
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    old = datetime.now(timezone.utc) - timedelta(days=20)
    lead = _lead(created_at=old, deck_wait_started_at=old)

    # park_count=4 -> promotions_so_far=3, over the cap of 2.
    plan = rad.plan_rebalance([{"lead": lead, "park_count": 4}])

    assert plan["counts"] == {"queue": 0, "maybe_placeholder": 1, "none": 0}


def test_plan_queues_lead_past_cap_when_website_present_conservative(monkeypatch):
    """A non-empty website means a real assessment might still find usable
    content via a scrape (which this offline planning step can't check) --
    so it's queued for a real attempt instead of being placeholder'd."""
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    old = datetime.now(timezone.utc) - timedelta(days=20)
    lead = _lead(created_at=old, deck_wait_started_at=old, website="https://acme.test")

    plan = rad.plan_rebalance([{"lead": lead, "park_count": 4}])

    assert plan["counts"] == {"queue": 1, "maybe_placeholder": 0, "none": 0}


def test_plan_queues_lead_past_cap_with_substantial_description_conservative(monkeypatch):
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    old = datetime.now(timezone.utc) - timedelta(days=20)
    lead = _lead(
        created_at=old, deck_wait_started_at=old,
        description="A well-funded team building proprietary industrial sensor hardware.",
    )

    plan = rad.plan_rebalance([{"lead": lead, "park_count": 4}])

    assert plan["counts"] == {"queue": 1, "maybe_placeholder": 0, "none": 0}


def test_plan_leaves_fresh_lead_alone(monkeypatch):
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    lead = _lead(created_at=recent, deck_wait_started_at=recent)

    plan = rad.plan_rebalance([{"lead": lead, "park_count": 1}])

    assert plan["counts"] == {"queue": 0, "maybe_placeholder": 0, "none": 1}


def test_plan_empty_when_no_rows():
    plan = rad.plan_rebalance([])
    assert plan == {
        "queue": [], "maybe_placeholder": [], "none": [],
        "counts": {"queue": 0, "maybe_placeholder": 0, "none": 0},
    }


# --- DB access ----------------------------------------------------------------

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.committed = 0
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def test_fetch_rows_computes_park_counts_from_lead_events():
    lead_a = _lead()
    lead_b = _lead()
    session = _FakeSession([[lead_a, lead_b], [(lead_a.id, 4), (lead_b.id, 1)]])

    rows = asyncio.run(rad._fetch_rows(session))

    by_id = {row["lead"].id: row["park_count"] for row in rows}
    assert by_id == {lead_a.id: 4, lead_b.id: 1}


def test_fetch_rows_empty_when_no_awaiting_deck_leads():
    session = _FakeSession([[]])

    rows = asyncio.run(rad._fetch_rows(session))

    assert rows == []


# --- apply_rebalance -----------------------------------------------------------

def test_apply_rebalance_queues_and_placeholders_isolating_failures(monkeypatch):
    lead_queue = _lead(company_name="QueueCo")
    lead_placeholder = _lead(company_name="PlaceholderCo")
    rows = [
        {"lead": lead_queue, "park_count": 1},
        {"lead": lead_placeholder, "park_count": 4},
    ]
    plan = {
        "queue": [{"id": str(lead_queue.id), "company_name": "QueueCo"}],
        "maybe_placeholder": [{"id": str(lead_placeholder.id), "company_name": "PlaceholderCo"}],
        "none": [],
        "counts": {"queue": 1, "maybe_placeholder": 1, "none": 0},
    }
    queued = []
    monkeypatch.setattr(rad.assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))
    session = _FakeSession([None])  # the single AssessmentCard lookup inside the placeholder write

    result = asyncio.run(rad.apply_rebalance(session, rows, plan))

    assert queued == [str(lead_queue.id)]
    assert result["queued_ok"] == [str(lead_queue.id)]
    assert result["placeholder_ok"] == [str(lead_placeholder.id)]
    assert lead_placeholder.status == "assessed"
    cards = [obj for obj in session.added if getattr(obj, "bucket", None) == "MAYBE"]
    assert len(cards) == 1
    assert cards[0].confidence_score == 0


def test_apply_rebalance_isolates_queue_failure(monkeypatch):
    lead_queue = _lead(company_name="FlakyCo")
    rows = [{"lead": lead_queue, "park_count": 1}]
    plan = {
        "queue": [{"id": str(lead_queue.id), "company_name": "FlakyCo"}],
        "maybe_placeholder": [],
        "none": [],
        "counts": {"queue": 1, "maybe_placeholder": 0, "none": 0},
    }

    def _boom(lead_id):
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(rad.assess_lead_task, "delay", _boom)
    session = _FakeSession([])

    result = asyncio.run(rad.apply_rebalance(session, rows, plan))

    assert result["queued_ok"] == []
    assert result["queued_failed"] == [str(lead_queue.id)]


# --- main(): dry-run vs --commit ---------------------------------------------

def _stub_session(monkeypatch, results):
    session = _FakeSession(results)
    monkeypatch.setattr(rad, "AsyncSessionLocal", lambda: session)
    return session


def test_main_dry_run_makes_no_writes(monkeypatch, capsys):
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    old = datetime.now(timezone.utc) - timedelta(days=10)
    lead = _lead(company_name="Acme", created_at=old, deck_wait_started_at=old)
    session = _stub_session(monkeypatch, [[lead], [(lead.id, 1)]])

    exit_code = rad.main([])

    assert exit_code == 0
    assert session.committed == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Acme" in out


def test_main_commit_with_nothing_to_do_makes_no_writes(monkeypatch, capsys):
    session = _stub_session(monkeypatch, [[]])

    exit_code = rad.main(["--commit"])

    assert exit_code == 0
    assert session.committed == 0
    assert "Nothing to do." in capsys.readouterr().out


def test_main_commit_queues_past_grace_lead(monkeypatch, capsys):
    monkeypatch.setattr(settings, "deck_grace_period_days", 2)
    monkeypatch.setattr(settings, "max_deck_promotions", 2)
    old = datetime.now(timezone.utc) - timedelta(days=10)
    lead = _lead(company_name="Acme", created_at=old, deck_wait_started_at=old)
    _stub_session(monkeypatch, [[lead], [(lead.id, 1)]])
    queued = []
    monkeypatch.setattr(rad.assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    exit_code = rad.main(["--commit"])

    assert exit_code == 0
    assert queued == [str(lead.id)]
    out = capsys.readouterr().out
    assert "Done. 1 queued, 0 placeholder'd, 0 failed." in out
