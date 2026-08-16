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

    async def commit(self):
        pass


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


def test_reaped_lead_is_not_reenqueued_by_an_immediately_following_run(monkeypatch):
    """Regression: a lead re-enqueued in one reaper pass must not still look
    stale to the very next pass, or every not-yet-started lead gets
    re-enqueued on every beat cycle until a worker catches up."""
    stale = _fake_lead("processing", age_minutes=25)

    monkeypatch.setattr(reap_stuck_leads.settings, "assessment_reap_after_minutes", 20)
    monkeypatch.setattr(reap_stuck_leads, "CelerySessionLocal", lambda: _FakeRunSession([stale]))
    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    first_result = asyncio.run(reap_stuck_leads._run())
    assert first_result["reaped"] == 1
    assert queued == [str(stale.id)]

    second_result = asyncio.run(reap_stuck_leads._run())
    assert second_result == {"checked": 1, "reaped": 0, "processing": 0, "pending": 0}
    assert queued == [str(stale.id)]


def test_lead_with_no_updated_at_is_treated_as_stale(monkeypatch):
    """Defensive: a null updated_at (shouldn't happen given the column's
    server_default, but never trust it) is reaped rather than skipped, so a
    bad row can't hide from the reaper forever."""
    lead = _fake_lead("processing", age_minutes=0)
    lead.updated_at = None

    result, queued = _run_reaper(monkeypatch, [lead])

    assert queued == [str(lead.id)]
    assert result["reaped"] == 1


def test_batch_is_capped_and_prioritizes_oldest(monkeypatch):
    """A backlog bigger than REAP_BATCH_LIMIT must not all fire at once --
    only the cap's worth goes out, oldest (most orphaned) leads first, so a
    thundering herd of re-enqueues can't hit the workers in one beat."""
    extra = 7
    total = reap_stuck_leads.REAP_BATCH_LIMIT + extra
    # All ages comfortably exceed the (default 20-minute) threshold, so every
    # lead is stale and only the batch cap decides how many get re-enqueued.
    leads = [_fake_lead("pending", age_minutes=1000 - i) for i in range(total)]
    # Oldest `REAP_BATCH_LIMIT` leads are the first ones in the list (highest age_minutes).
    expected_ids = {str(lead.id) for lead in leads[:reap_stuck_leads.REAP_BATCH_LIMIT]}

    result, queued = _run_reaper(monkeypatch, leads)

    assert result["reaped"] == reap_stuck_leads.REAP_BATCH_LIMIT
    assert len(queued) == reap_stuck_leads.REAP_BATCH_LIMIT
    assert set(queued) == expected_ids


def test_zero_reap_after_minutes_falls_back_to_default(monkeypatch):
    """A misconfigured 0 (or falsy) assessment_reap_after_minutes must not
    turn into 'reap everything that's pending/processing' -- it should fall
    back to DEFAULT_REAP_AFTER_MINUTES instead."""
    just_queued = _fake_lead("pending", age_minutes=1)

    result, queued = _run_reaper(monkeypatch, [just_queued], reap_after_minutes=0)

    assert queued == []
    assert result["reaped"] == 0


def test_zero_reap_after_minutes_still_reaps_leads_older_than_default(monkeypatch):
    """Complement to the above: with the 0 -> default(30) fallback applied,
    a lead older than the default threshold is still reaped."""
    old_enough = _fake_lead("processing", age_minutes=reap_stuck_leads.DEFAULT_REAP_AFTER_MINUTES + 5)

    result, queued = _run_reaper(monkeypatch, [old_enough], reap_after_minutes=0)

    assert queued == [str(old_enough.id)]
    assert result["reaped"] == 1
