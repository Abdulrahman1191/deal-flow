"""
assess_lead must never move an archived lead out of `archived`.

Seen in production (2026-09-29): a lead archived by copper_reconcile at
22:41:25 was flipped to `awaiting_deck` at 22:43:43 by an assess_lead task
queued before the archive, putting it back on the Awaiting Deck tab until the
next reconcile. The `assessed` writes already guarded against this; the park
(`awaiting_deck`) and `processing` writes did not.
"""
from __future__ import annotations
import asyncio
import uuid

from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.models.user import User
from app.tasks import assess_lead


class _StatusRecordingLead:
    """A lead double that records every status it is set to."""

    def __init__(self, **fields):
        object.__setattr__(self, "status_history", [])
        for k, v in fields.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, name, value):
        if name == "status":
            self.status_history.append(value)
        object.__setattr__(self, name, value)


def _archived_lead(**overrides):
    fields = dict(
        id=uuid.uuid4(), status="archived", pitch_deck_text=None,
        company_linkedin_url="https://linkedin.com/company/acme", company_name="Acme Deep Tech",
        website=None, description="", stage="seed", region="MENA", founder_names=["Founder One"],
        linkedin_urls=None, copper_id="94808710", raw_copper_data=None, owner_email=None,
        assessment_attempts=0, deck_promotion_count=0,
    )
    fields.update(overrides)
    return _StatusRecordingLead(**fields)


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    def __init__(self, lead):
        self.lead = lead
        self.added = []
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _FakeScalarResult(self.lead)
        if entity is User:
            return _FakeScalarResult(None)
        assert entity is AssessmentCard
        return _FakeScalarResult(None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def _boom(*_a, **_k):
    raise AssertionError("an archived, context-less lead must not be researched or assessed")


def test_archived_lead_with_no_context_is_not_parked(monkeypatch):
    lead = _archived_lead()
    session = _FakeSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", lambda website: "")
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _boom)
    monkeypatch.setattr(assess_lead.research, "research_company", _boom)

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "skipped_archived"}
    assert lead.status == "archived"
    assert lead.status_history == []
    assert [getattr(o, "event_type", None) for o in session.added] == []


def test_archived_lead_with_context_is_assessed_but_stays_archived(monkeypatch):
    lead = _archived_lead(description="A well-funded team building proprietary industrial sensor hardware for MENA factories.")
    session = _FakeSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {})
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", lambda lead_data, research_data, **kw: {
        "bucket": "MAYBE", "confidence_score": 35, "summary": "s", "positive_signals": [], "red_flags": [],
        "data_gaps": [], "scoring_breakdown": {}, "draft_subject": None, "draft_body": None,
        "draft_type": None, "research_sources": [], "precedents_cited": [],
    })
    import app.services.feedback_patterns as feedback_patterns

    async def _no_exemplars(*_a, **_k):
        return []
    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _no_exemplars)
    monkeypatch.setattr(assess_lead.copper_writer, "push_assessment", lambda *a, **k: None)

    asyncio.run(assess_lead._run(str(lead.id)))

    assert lead.status == "archived"
    assert "processing" not in lead.status_history
    assert "awaiting_deck" not in lead.status_history
    assert any(isinstance(o, AssessmentCard) for o in session.added)
