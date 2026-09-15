"""
Tests for persisting *why* an assessment failed (issue #163).

Incident (2026-09-15): 367 leads firm-wide stuck in status='failed' with no
way to diagnose the cause -- the real exception was only print()ed by
_mark_failed, never persisted. _mark_failed now also writes
leads.last_assessment_error/_at and logs an `assessment_failed` LeadEvent
with {error, attempt}; _run() clears both fields on the next clean outcome
(assessed / awaiting_deck) so a lead that recovers doesn't keep showing a
now-irrelevant error.

_mark_failed's sync engine is exercised against a fake SQLAlchemy engine
(mirrors test_mark_failed_normalizes_asyncpg_ssl_param_via_shared_helper in
test_copper_writebacks.py) so no live Postgres is needed; _run() is
exercised against the fake CelerySessionLocal session pattern used
throughout test_assess_lead_awaiting_deck.py.
"""
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import sqlalchemy
from celery.exceptions import SoftTimeLimitExceeded

from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.tasks import assess_lead


class _FakeConn:
    def __init__(self):
        self.statements: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.statements.append((str(stmt), params or {}))


class _FakeEngine:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def begin(self):
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc_info):
        return False


def _patch_engine(monkeypatch) -> _FakeConn:
    conn = _FakeConn()
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **k: _FakeEngine(conn))
    return conn


# ---------------------------------------------------------------------------
# _format_error: repr(exc) + trimmed traceback tail
# ---------------------------------------------------------------------------


def test_format_error_includes_repr_and_traceback():
    try:
        raise ValueError("data-specific failure")
    except ValueError as exc:
        formatted = assess_lead._format_error(exc)

    assert "ValueError" in formatted
    assert "data-specific failure" in formatted
    assert "test_assess_lead_failure_reason.py" in formatted


def test_format_error_is_trimmed_to_max_chars():
    try:
        raise ValueError("x" * (assess_lead.MAX_ERROR_CHARS * 3))
    except ValueError as exc:
        formatted = assess_lead._format_error(exc)

    assert len(formatted) <= assess_lead.MAX_ERROR_CHARS


# ---------------------------------------------------------------------------
# _mark_failed: persists last_assessment_error/_at + logs assessment_failed
# ---------------------------------------------------------------------------


def test_mark_failed_persists_error_and_logs_event(monkeypatch):
    lead_id = str(uuid.uuid4())
    conn = _patch_engine(monkeypatch)

    assess_lead._mark_failed(lead_id, "RuntimeError('boom')\nTraceback (most recent call last):", attempt=2)

    assert len(conn.statements) == 2

    update_sql, update_params = conn.statements[0]
    assert "UPDATE leads" in update_sql
    assert "last_assessment_error" in update_sql
    assert "last_assessment_error_at" in update_sql
    # Fixed status guard (issue #163 item 4): a lead promoted straight from
    # awaiting_deck that crashes before _run() flips it to 'processing' must
    # still land in 'failed' with the reason recorded.
    assert "awaiting_deck" in update_sql
    assert update_params["lid"] == lead_id
    assert "RuntimeError" in update_params["err"]

    insert_sql, insert_params = conn.statements[1]
    assert "lead_events" in insert_sql
    assert "assessment_failed" in insert_sql
    assert insert_params["lid"] == lead_id
    payload = json.loads(insert_params["payload"])
    assert payload == {"error": "RuntimeError('boom')\nTraceback (most recent call last):", "attempt": 2}


def test_mark_failed_trims_long_errors_before_persisting(monkeypatch):
    lead_id = str(uuid.uuid4())
    conn = _patch_engine(monkeypatch)

    huge_error = "x" * (assess_lead.MAX_ERROR_CHARS * 2)
    assess_lead._mark_failed(lead_id, huge_error)

    _, update_params = conn.statements[0]
    assert len(update_params["err"]) == assess_lead.MAX_ERROR_CHARS
    _, insert_params = conn.statements[1]
    payload = json.loads(insert_params["payload"])
    assert len(payload["error"]) == assess_lead.MAX_ERROR_CHARS


def test_soft_time_limit_exceeded_persists_formatted_error(monkeypatch):
    """Forces an exception inside _run (SoftTimeLimitExceeded, same trigger
    as test_soft_time_limit_exceeded_marks_failed_instead_of_crashing in
    test_assess_lead_attempts.py) and, unlike that test, lets the real
    _mark_failed run against a fake sync engine to confirm the formatted
    error and event actually reach the DB layer."""
    lead_id = str(uuid.uuid4())
    monkeypatch.setattr(assess_lead, "_increment_attempts", lambda lid: 1)

    async def _fake_run(lid):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(assess_lead, "_run", _fake_run)
    conn = _patch_engine(monkeypatch)

    result = assess_lead.assess_lead_task(lead_id)

    assert result["status"] == "failed"
    _, update_params = conn.statements[0]
    assert "SoftTimeLimitExceeded" in update_params["err"]
    _, insert_params = conn.statements[1]
    payload = json.loads(insert_params["payload"])
    assert payload["attempt"] == 1
    assert "SoftTimeLimitExceeded" in payload["error"]


# ---------------------------------------------------------------------------
# _run(): a clean outcome clears a stale last_assessment_error/_at
# ---------------------------------------------------------------------------


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeRunSession:
    def __init__(self, lead, card=None, owner=None):
        self.lead = lead
        self.card = card
        self.owner = owner
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        from app.models.user import User

        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _FakeScalarResult(self.lead)
        if entity is User:
            return _FakeScalarResult(self.owner)
        assert entity is AssessmentCard
        return _FakeScalarResult(self.card)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _fake_lead_with_stale_error(**overrides):
    base = dict(
        id=uuid.uuid4(),
        status="pending",
        pitch_deck_text="Deck contents go here " * 50,
        company_linkedin_url="https://linkedin.com/company/acme",
        company_name="Acme Deep Tech",
        website="https://acme.test",
        description="A deep-tech startup.",
        stage="seed",
        region="MENA",
        founder_names=["Founder One"],
        linkedin_urls=None,
        copper_id=None,
        raw_copper_data=None,
        owner_email=None,
        assessment_attempts=2,
        last_assessment_error="RuntimeError('boom')\nold traceback",
        last_assessment_error_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_successful_assessment_clears_stale_error(monkeypatch):
    lead = _fake_lead_with_stale_error()
    session = _FakeRunSession(lead, card=None)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})

    assessment_result = {
        "bucket": "YES",
        "confidence_score": 80,
        "summary": "Strong team.",
        "positive_signals": [],
        "red_flags": [],
        "data_gaps": [],
        "scoring_breakdown": {},
        "draft_subject": "Let's talk",
        "draft_body": "Hi there",
        "draft_type": "meeting_request",
        "research_sources": [],
        "precedents_cited": [],
    }
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", lambda *a, **k: assessment_result)

    import app.services.feedback_patterns as feedback_patterns

    async def _fake_exemplars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _fake_exemplars)

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result["bucket"] == "YES"
    assert lead.status == "assessed"
    assert lead.last_assessment_error is None
    assert lead.last_assessment_error_at is None


def test_awaiting_deck_park_clears_stale_error(monkeypatch):
    """A lead that re-parks in awaiting_deck (e.g. its deck was removed) is
    not a failure state -- any error left over from a prior failed attempt
    must still be cleared, not linger indefinitely."""
    lead = _fake_lead_with_stale_error(pitch_deck_text=None, website=None, description="")
    session = _FakeRunSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", lambda website: "")

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result["status"] == "awaiting_deck"
    assert lead.last_assessment_error is None
    assert lead.last_assessment_error_at is None
