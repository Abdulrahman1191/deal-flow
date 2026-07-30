"""
Tests for the orphaned-assessment reaper (issue #100).

reap_stuck_leads._run() must re-enqueue assess_lead_task for any lead stuck in
'processing' or 'pending' whose updated_at is older than
settings.assessment_reap_after_minutes, and leave everything else alone.
Exercised against a fake CelerySessionLocal async-context-manager session
(mirrors the _FakeRunSession pattern in test_sync_pitch_decks.py) so no live
Postgres is needed.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.lead import Lead
from app.tasks import reap_stuck_leads
from app.tasks.assess_lead import assess_lead_task


class _FakeLeadsResult:
    def __init__(self, leads):
        self._leads = leads

    def scalars(self):
        return self

    def all(self):
        return self._leads


class _FakeRunSession:
    """Stands in for CelerySessionLocal()'s async context manager -- the
    only query _run() issues is select(Lead), so every execute() answers
    with the fixed `leads` list."""

    def __init__(self, leads):
        self._leads = leads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        assert entity is Lead
        return _FakeLeadsResult(self._leads)


def _fake_lead(status, age_minutes, lead_id=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        status=status,
        updated_at=now - timedelta(minutes=age_minutes),
    )


def _run_reaper(monkeypatch, leads, reap_after_minutes=20):
    monkeypatch.setattr(reap_stuck_leads.settings, "assessment_reap_after_minutes", reap_after_minutes)
    monkeypatch.setattr(reap_stuck_leads, "CelerySessionLocal", lambda: _FakeRunSession(leads))

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(reap_stuck_leads._run())
    return result, queued


def test_stale_processing_lead_is_reenqueued(monkeypatch):
    stale = _fake_lead("processing", age_minutes=25)
    result, queued = _run_reaper(monkeypatch, [stale])

    assert queued == [str(stale.id)]
    assert result == {"checked": 1, "reaped": 1, "processing": 1, "pending": 0}


def test_recent_processing_lead_is_left_alone(monkeypatch):
    fresh = _fake_lead("processing", age_minutes=5)
    result, queued = _run_reaper(monkeypatch, [fresh])

    assert queued == []
    assert result == {"checked": 1, "reaped": 0, "processing": 0, "pending": 0}


def test_stale_pending_lead_is_reenqueued(monkeypatch):
    stale_pending = _fake_lead("pending", age_minutes=30)
    result, queued = _run_reaper(monkeypatch, [stale_pending])

    assert queued == [str(stale_pending.id)]
    assert result == {"checked": 1, "reaped": 1, "processing": 0, "pending": 1}


def test_recent_pending_lead_is_left_alone(monkeypatch):
    fresh_pending = _fake_lead("pending", age_minutes=1)
    result, queued = _run_reaper(monkeypatch, [fresh_pending])

    assert queued == []
    assert result == {"checked": 1, "reaped": 0, "processing": 0, "pending": 0}


def test_mixed_batch_only_reenqueues_stale_ones(monkeypatch):
    stale_processing = _fake_lead("processing", age_minutes=100)
    fresh_processing = _fake_lead("processing", age_minutes=2)
    stale_pending = _fake_lead("pending", age_minutes=21)
    fresh_pending = _fake_lead("pending", age_minutes=19)

    result, queued = _run_reaper(
        monkeypatch, [stale_processing, fresh_processing, stale_pending, fresh_pending]
    )

    assert set(queued) == {str(stale_processing.id), str(stale_pending.id)}
    assert result == {"checked": 4, "reaped": 2, "processing": 1, "pending": 1}


def test_threshold_is_configurable(monkeypatch):
    """A lead that's stale under the default 20-minute threshold is left
    alone once the threshold is widened past its age."""
    lead = _fake_lead("processing", age_minutes=25)
    result, queued = _run_reaper(monkeypatch, [lead], reap_after_minutes=30)

    assert queued == []
    assert result["reaped"] == 0


def test_lead_with_no_updated_at_is_treated_as_stale(monkeypatch):
    """Defensive: a null updated_at (shouldn't happen given the column's
    server_default, but never trust it) is reaped rather than skipped, so a
    bad row can't hide from the reaper forever."""
    lead = _fake_lead("processing", age_minutes=0)
    lead.updated_at = None

    result, queued = _run_reaper(monkeypatch, [lead])

    assert queued == [str(lead.id)]
    assert result["reaped"] == 1
