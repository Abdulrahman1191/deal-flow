"""
Tests for the "park deckless leads instead of rejecting them" gate (issue #67).

assess_lead._run must, before doing ANY research or calling the LLM, check
for usable pitch_deck_text. No text -> park the lead in `awaiting_deck`, log
a LeadEvent, and return early (no cost, no AssessmentCard). Deck text present
-> score exactly as before and land on `assessed`.

Exercised against a fake CelerySessionLocal async-context-manager session
(mirrors the _FakeRunSession pattern in test_sync_pitch_decks.py) so no live
Postgres is needed.
"""
from __future__ import annotations
import asyncio
import uuid
from types import SimpleNamespace

from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.tasks import assess_lead


class _FakeLeadResult:
    def __init__(self, lead):
        self._lead = lead

    def scalar_one_or_none(self):
        return self._lead


class _FakeCardResult:
    def __init__(self, card):
        self._card = card

    def scalar_one_or_none(self):
        return self._card


class _FakeSession:
    """Stands in for CelerySessionLocal()'s async context manager. The first
    execute() (select(Lead)) answers with `lead`; any later execute() (the
    assessment-card upsert lookup) answers with `card`."""

    def __init__(self, lead, card=None):
        self.lead = lead
        self.card = card
        self.added: list = []
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _FakeLeadResult(self.lead)
        assert entity is AssessmentCard
        return _FakeCardResult(self.card)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def _fake_lead(pitch_deck_text=None, status="pending"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        pitch_deck_text=pitch_deck_text,
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
    )


def _boom(*_args, **_kwargs):
    raise AssertionError("must not be called for a lead with no pitch deck text")


def test_gate_parks_deckless_lead_without_scoring(monkeypatch):
    lead = _fake_lead(pitch_deck_text=None)
    session = _FakeSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _boom)
    monkeypatch.setattr(assess_lead.research, "research_company", _boom)
    monkeypatch.setattr(assess_lead.research, "scrape_linkedin_from_website", _boom)
    monkeypatch.setattr(assess_lead.research, "find_linkedin_via_llm_search", _boom)

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "awaiting_deck"}
    assert lead.status == "awaiting_deck"
    # No AssessmentCard is created or touched -- only the LeadEvent is added.
    assert len(session.added) == 1
    event = session.added[0]
    assert event.event_type == "awaiting_deck"
    assert event.lead_id == lead.id
    assert session.committed == 1


def test_gate_also_parks_lead_with_empty_string_deck_text(monkeypatch):
    """Empty string is falsy just like None -- `not lead.pitch_deck_text`
    covers both, matching the backfill script's OR-null-or-empty filter."""
    lead = _fake_lead(pitch_deck_text="")
    session = _FakeSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _boom)

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result["status"] == "awaiting_deck"
    assert lead.status == "awaiting_deck"


def test_lead_with_deck_text_scores_normally_and_lands_on_assessed(monkeypatch):
    lead = _fake_lead(pitch_deck_text="Deck contents go here " * 50)
    session = _FakeSession(lead, card=None)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)

    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})

    assessment_result = {
        "bucket": "YES",
        "confidence_score": 82,
        "summary": "Strong team, real deck.",
        "positive_signals": ["experienced founders"],
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

    assert lead.status == "assessed"
    assert result["bucket"] == "YES"
    assert result["confidence_score"] == 82
    # A card was created (added) since none existed yet.
    cards = [obj for obj in session.added if isinstance(obj, AssessmentCard)]
    assert len(cards) == 1
    assert cards[0].bucket == "YES"
