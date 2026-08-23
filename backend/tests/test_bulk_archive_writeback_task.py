"""
Tests for app.tasks.bulk_archive_writeback (issue #141 follow-up): the
per-lead Copper write-back dispatched by POST /leads/bulk-archive.

Auto-generates the Unqualification Reasons/Details via the LLM the same way
the single archive-no-reply path does, then calls copper_writer.archive_in_copper
with them -- but as a background Celery task (one per lead) instead of inline
in the request, so a large batch's LLM calls don't block the HTTP response.
Best-effort throughout: no assessment card, or a failed AI call, still ends
in a Copper write-back (just without the reason fields).

Mirrors the _FakeTaskSession pattern from test_owner_calendly_draft.py /
test_assess_lead_awaiting_deck.py.
"""
from __future__ import annotations
import asyncio
import uuid
from types import SimpleNamespace

from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services import claude_agent, copper_writer
from app.tasks import bulk_archive_writeback


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeTaskSession:
    def __init__(self, lead, card=None):
        self.lead = lead
        self.card = card

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _FakeScalarResult(self.lead)
        assert entity is AssessmentCard
        return _FakeScalarResult(self.card)


def _fake_lead(copper_id="7", copper_opportunity_id=None, lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        raw_copper_data={"tags": ["existing-tag"]},
    )


def _fake_card(bucket="REJECT"):
    return SimpleNamespace(
        bucket=bucket,
        user_override=None,
        summary="thin deep-tech signal",
        red_flags=[],
    )


def test_writeback_task_generates_reason_and_archives_in_copper(monkeypatch):
    lead = _fake_lead()
    card = _fake_card()
    session = _FakeTaskSession(lead, card=card)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {"reason_option_ids": [367301], "detail_text": "Out of region."},
    )

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    result = asyncio.run(bulk_archive_writeback._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "written_back"}
    assert calls == [
        ("7", ["existing-tag"], {"reason_option_ids": [367301], "detail_text": "Out of region."})
    ]


def test_writeback_task_ai_failure_is_best_effort_and_still_writes_back(monkeypatch):
    lead = _fake_lead()
    card = _fake_card()
    session = _FakeTaskSession(lead, card=card)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)

    def _boom(**kwargs):
        raise RuntimeError("deepseek down")

    monkeypatch.setattr(claude_agent, "generate_unqualification_reason", _boom)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    result = asyncio.run(bulk_archive_writeback._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "written_back"}
    assert calls == [("7", ["existing-tag"], {"reason_option_ids": None, "detail_text": None})]


def test_writeback_task_no_card_still_writes_back_without_reason(monkeypatch):
    lead = _fake_lead()
    session = _FakeTaskSession(lead, card=None)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    result = asyncio.run(bulk_archive_writeback._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "written_back"}
    assert calls == [("7", ["existing-tag"], {"reason_option_ids": None, "detail_text": None})]


def test_writeback_task_skips_when_lead_not_found(monkeypatch):
    session = _FakeTaskSession(None)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)

    lead_id = str(uuid.uuid4())
    result = asyncio.run(bulk_archive_writeback._run(lead_id))

    assert result == {"lead_id": lead_id, "status": "skipped"}


def test_writeback_task_skips_when_no_copper_id(monkeypatch):
    lead = _fake_lead(copper_id=None)
    session = _FakeTaskSession(lead)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)

    result = asyncio.run(bulk_archive_writeback._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "skipped"}


def test_writeback_task_skips_when_already_converted_to_opportunity(monkeypatch):
    lead = _fake_lead(copper_opportunity_id="opp-1")
    session = _FakeTaskSession(lead)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)

    result = asyncio.run(bulk_archive_writeback._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "skipped"}


def test_writeback_task_wrapper_catches_exceptions_and_returns_failed(monkeypatch):
    def _boom(_lead_id):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(bulk_archive_writeback, "_run", lambda lead_id: _boom(lead_id))

    result = bulk_archive_writeback.bulk_archive_writeback_task("some-lead-id")

    assert result["lead_id"] == "some-lead-id"
    assert result["status"] == "failed"
    assert "db exploded" in result["error"]
