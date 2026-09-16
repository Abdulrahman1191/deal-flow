"""
Tests for the failed-assessment auto-recovery backstop (issue #163).

redrive_failed_assessments._run() must re-queue 'failed' leads whose
last_assessment_error_at is older than REDRIVE_AFTER_HOURS, resetting
assessment_attempts, and never touch a lead past its per-lead
assessment_failed_redrives cap (settings.assessment_failed_max_redrives) or
one that isn't 'failed' -- which also excludes archived leads, since status
is a single column and a lead currently 'archived' can never simultaneously
be 'failed'.

Exercised against a fake CelerySessionLocal async-context-manager session,
mirroring the _FakeRunSession/_FilteringFakeSession patterns in
test_reap_stuck_leads.py and test_redrive_outbox.py, so no live Postgres is
needed.
"""
from __future__ import annotations
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.tasks import redrive_failed_assessments as rfa
from app.tasks.assess_lead import assess_lead_task


class _FakeLeadsResult:
    def __init__(self, leads):
        self._leads = leads

    def scalars(self):
        return self

    def all(self):
        return self._leads


class _FakeSession:
    """Returns the fixed `leads` list regardless of the query -- used for
    tests that only exercise _run()'s Python-side cutoff filtering, on the
    assumption the SQL WHERE (status/cap) has already narrowed the
    candidates, as it would against a real DB."""

    def __init__(self, leads):
        self._leads = leads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeLeadsResult(self._leads)

    async def commit(self):
        pass


class _FilteringFakeSession:
    """More faithful than _FakeSession -- actually applies the query's
    status/redrive-cap WHERE and LIMIT against the full lead list by
    reading them out of the compiled SQL (mirrors test_redrive_outbox.py's
    _FilteringFakeSession). Proves the SQL-level exclusion of archived
    leads and at/over-cap leads keeps them out of the working set entirely,
    not just skipped in Python after fetching."""

    def __init__(self, leads):
        self._leads = leads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        sql = str(query.compile(compile_kwargs={"literal_binds": True}))
        cap_match = re.search(r"assessment_failed_redrives < (\d+)", sql)
        assert cap_match, "expected an assessment_failed_redrives < <cap> filter in the query"
        cap = int(cap_match.group(1))
        limit_match = re.search(r"LIMIT (\d+)", sql)
        limit = int(limit_match.group(1)) if limit_match else len(self._leads)

        eligible = [
            lead for lead in self._leads
            if lead.status == "failed" and lead.assessment_failed_redrives < cap
        ]
        eligible.sort(key=lambda lead: lead.last_assessment_error_at or datetime.min.replace(tzinfo=timezone.utc))
        return _FakeLeadsResult(eligible[:limit])

    async def commit(self):
        pass


def _fake_lead(status="failed", hours_ago=10, redrives=0, lead_id=None):
    last_error_at = (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago) if hours_ago is not None else None
    )
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        status=status,
        assessment_attempts=3,
        assessment_failed_redrives=redrives,
        last_assessment_error_at=last_error_at,
    )


def _run_redrive(monkeypatch, leads, max_redrives=3, session_cls=_FakeSession):
    monkeypatch.setattr(rfa.settings, "assessment_failed_max_redrives", max_redrives)
    monkeypatch.setattr(rfa, "CelerySessionLocal", lambda: session_cls(leads))

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(rfa._run())
    return result, queued


def test_stale_failed_lead_is_redriven(monkeypatch):
    lead = _fake_lead(hours_ago=10)
    result, queued = _run_redrive(monkeypatch, [lead])

    assert queued == [str(lead.id)]
    assert lead.status == "pending"
    assert lead.assessment_attempts == 0
    assert lead.assessment_failed_redrives == 1
    assert result == {"checked": 1, "redriven": 1}


def test_recently_failed_lead_is_left_alone(monkeypatch):
    lead = _fake_lead(hours_ago=1)
    result, queued = _run_redrive(monkeypatch, [lead])

    assert queued == []
    assert lead.status == "failed"
    assert lead.assessment_failed_redrives == 0
    assert result == {"checked": 1, "redriven": 0}


def test_null_last_assessment_error_at_is_treated_as_eligible(monkeypatch):
    """A pre-existing 'failed' lead from before this column existed is
    redriven immediately rather than waiting forever for a timestamp that
    will never be set -- exactly the 367-lead incident this task exists to
    unstick."""
    lead = _fake_lead(hours_ago=None)
    result, queued = _run_redrive(monkeypatch, [lead])

    assert queued == [str(lead.id)]
    assert lead.status == "pending"
    assert result["redriven"] == 1


def test_mixed_batch_only_redrives_eligible_ones(monkeypatch):
    stale = _fake_lead(hours_ago=10)
    fresh = _fake_lead(hours_ago=1)

    result, queued = _run_redrive(monkeypatch, [stale, fresh])

    assert queued == [str(stale.id)]
    assert result == {"checked": 2, "redriven": 1}


def test_redrive_cap_is_enforced_at_sql_level(monkeypatch):
    at_cap = _fake_lead(hours_ago=100, redrives=3)
    redrivable = _fake_lead(hours_ago=100, redrives=1)

    result, queued = _run_redrive(
        monkeypatch, [at_cap, redrivable], max_redrives=3, session_cls=_FilteringFakeSession
    )

    assert redrivable.status == "pending"
    assert redrivable.assessment_failed_redrives == 2
    assert at_cap.status == "failed"
    assert at_cap.assessment_failed_redrives == 3
    assert set(queued) == {str(redrivable.id)}
    assert result == {"checked": 1, "redriven": 1}


def test_archived_lead_is_never_redriven(monkeypatch):
    """status is a single column -- a lead currently 'archived' can never
    also match status == 'failed', so the SQL WHERE excludes it outright."""
    archived = _fake_lead(status="archived", hours_ago=100)
    failed = _fake_lead(hours_ago=100)

    result, queued = _run_redrive(monkeypatch, [archived, failed], session_cls=_FilteringFakeSession)

    assert archived.status == "archived"
    assert archived.assessment_failed_redrives == 0
    assert queued == [str(failed.id)]
    assert result == {"checked": 1, "redriven": 1}


def test_zero_max_redrives_falls_back_to_default(monkeypatch):
    """A misconfigured 0 (or unset) assessment_failed_max_redrives must not
    turn into 'never redrive anything' -- it should fall back to
    DEFAULT_MAX_REDRIVES. Proven at the SQL level: with redrives=2, a raw
    cap of 0 would exclude this lead (2 < 0 is False); the fallback to 3
    includes it (2 < 3 is True)."""
    lead = _fake_lead(hours_ago=10, redrives=2)

    result, queued = _run_redrive(monkeypatch, [lead], max_redrives=0, session_cls=_FilteringFakeSession)

    assert queued == [str(lead.id)]
    assert result["redriven"] == 1


def test_no_failed_leads_is_a_clean_no_op(monkeypatch):
    result, queued = _run_redrive(monkeypatch, [])

    assert queued == []
    assert result == {"checked": 0, "redriven": 0}
