"""
Tests for the grace-period fallback task (issue #149): a lead parked in
awaiting_deck whose wait started before settings.deck_grace_period_days ago
gets re-queued for assess_lead_task so it falls through to the #144
website/description assessment path instead of staying parked forever.

Mirrors the _FakeRunSession pattern in test_sync_pitch_decks.py: the actual
cutoff filtering happens in the SQL WHERE clause (select(Lead).where(...)),
so these tests stand in a fake session that returns whatever lead list the
test hands it -- as if that filter had already run -- and exercise what
_run() does with the result (reset the wait clock, queue assess_lead_task,
isolate per-lead enqueue failures).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.config import settings
from app.tasks import promote_awaiting_deck as pad
from app.tasks.assess_lead import assess_lead_task


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
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeLeadsResult(self._leads)

    async def commit(self):
        self.commits += 1


def _lead(**overrides):
    base = dict(
        id=uuid.uuid4(),
        status="awaiting_deck",
        deck_wait_started_at=None,
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_promotes_lead_past_grace_period(monkeypatch):
    monkeypatch.setattr(settings, "deck_grace_period_days", 5)
    stale = _lead(deck_wait_started_at=datetime.now(timezone.utc) - timedelta(days=6))

    monkeypatch.setattr(pad, "CelerySessionLocal", lambda: _FakeSession([stale]))
    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(pad._run())

    assert result == {"checked": 1, "promoted": 1}
    assert queued == [str(stale.id)]
    # The clock is reset on promotion so the very next sweep doesn't
    # immediately re-promote it again.
    assert stale.deck_wait_started_at > datetime.now(timezone.utc) - timedelta(minutes=1)


def test_no_stale_leads_is_a_clean_no_op(monkeypatch):
    monkeypatch.setattr(pad, "CelerySessionLocal", lambda: _FakeSession([]))
    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(pad._run())

    assert result == {"checked": 0, "promoted": 0}
    assert queued == []


def test_falls_back_to_created_at_when_deck_wait_started_at_is_null(monkeypatch):
    """A lead that entered awaiting_deck some other way than the brand-new
    import path may never have deck_wait_started_at set -- _fetch_stale_leads'
    query coalesces to created_at so it still ages out. Here that's simulated
    by the fake session simply returning it (as the real WHERE clause would),
    and this test confirms _run() promotes it like any other candidate."""
    stale = _lead(
        deck_wait_started_at=None,
        created_at=datetime.now(timezone.utc) - timedelta(days=10),
    )

    monkeypatch.setattr(pad, "CelerySessionLocal", lambda: _FakeSession([stale]))
    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(pad._run())

    assert result == {"checked": 1, "promoted": 1}
    assert queued == [str(stale.id)]
    assert stale.deck_wait_started_at is not None


def test_enqueue_failure_is_isolated_per_lead(monkeypatch):
    stale_a = _lead(deck_wait_started_at=datetime.now(timezone.utc) - timedelta(days=6))
    stale_b = _lead(deck_wait_started_at=datetime.now(timezone.utc) - timedelta(days=6))

    monkeypatch.setattr(pad, "CelerySessionLocal", lambda: _FakeSession([stale_a, stale_b]))

    queued = []

    def _delay(lead_id):
        if lead_id == str(stale_a.id):
            raise RuntimeError("redis unreachable")
        queued.append(lead_id)

    monkeypatch.setattr(assess_lead_task, "delay", _delay)

    result = asyncio.run(pad._run())

    assert result["checked"] == 2
    assert result["promoted"] == 1
    assert queued == [str(stale_b.id)]
