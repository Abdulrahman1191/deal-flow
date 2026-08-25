"""
Tests for the "park deckless leads instead of rejecting them" gate (issue #67),
now broadened (issue #144) so a deckless lead with a usable website or a
substantial description gets scored -- via scraped website content standing
in for the deck -- instead of being parked. assess_lead._run only parks a
lead in `awaiting_deck` when it has NO deck, NO usable scraped website
content, AND a thin/empty description.

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
from app.models.user import User
from app.tasks import assess_lead


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    """Stands in for CelerySessionLocal()'s async context manager. The first
    execute() (select(Lead)) answers with `lead`; a select(User) (owner
    lookup for draft Calendly/name, issue #84) answers with `owner`; any
    later execute() (the assessment-card upsert lookup) answers with `card`."""

    def __init__(self, lead, card=None, owner=None):
        self.lead = lead
        self.card = card
        self.owner = owner
        self.added: list = []
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
            return _FakeScalarResult(self.owner)
        assert entity is AssessmentCard
        return _FakeScalarResult(self.card)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def _fake_lead(pitch_deck_text=None, status="pending", owner_email=None, website="https://acme.test", description="A deep-tech startup."):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        pitch_deck_text=pitch_deck_text,
        company_linkedin_url="https://linkedin.com/company/acme",
        company_name="Acme Deep Tech",
        website=website,
        description=description,
        stage="seed",
        region="MENA",
        founder_names=["Founder One"],
        linkedin_urls=None,
        copper_id=None,
        raw_copper_data=None,
        owner_email=owner_email,
        # Nonzero so tests can assert _run() resets it on a clean outcome
        # (issue #129) -- a lead that eventually succeeds must not stay
        # poisoned by earlier transient-failure attempts.
        assessment_attempts=2,
    )


def _boom(*_args, **_kwargs):
    raise AssertionError("must not be called for a lead with no pitch deck text")


def test_gate_parks_lead_with_no_deck_no_website_content_and_thin_description(monkeypatch):
    lead = _fake_lead(pitch_deck_text=None, website=None, description="")
    session = _FakeSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _boom)
    monkeypatch.setattr(assess_lead.research, "research_company", _boom)
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", lambda website: "")
    monkeypatch.setattr(assess_lead.research, "scrape_linkedin_from_website", _boom)
    monkeypatch.setattr(assess_lead.research, "find_linkedin_via_llm_search", _boom)

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "awaiting_deck"}
    assert lead.status == "awaiting_deck"
    assert lead.assessment_attempts == 0
    # No AssessmentCard is created or touched -- only the LeadEvent is added.
    assert len(session.added) == 1
    event = session.added[0]
    assert event.event_type == "awaiting_deck"
    assert event.lead_id == lead.id
    assert session.committed == 1


def test_gate_also_parks_lead_with_empty_string_deck_text_no_website_thin_description(monkeypatch):
    """Empty string is falsy just like None -- `not lead.pitch_deck_text`
    covers both. With no website content and a thin description, this still
    has no usable context anywhere, so it still parks."""
    lead = _fake_lead(pitch_deck_text="", website=None, description="")
    session = _FakeSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _boom)
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", lambda website: "")

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result["status"] == "awaiting_deck"
    assert lead.status == "awaiting_deck"


def test_gate_scores_deckless_lead_with_usable_website_content_instead_of_parking(monkeypatch):
    """A deckless lead whose site scrapes real content should be assessed --
    not parked -- with the scraped text folded into research_data and the
    card flagged assessed_without_deck (issue #144)."""
    lead = _fake_lead(pitch_deck_text=None, website="https://acme.test", description="")
    session = _FakeSession(lead, card=None)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)

    scraped_text = "Acme Deep Tech builds proprietary sensors for industrial robotics. " * 3
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", lambda website: scraped_text)

    captured_research_data = {}

    def _fake_research_company(lead_data):
        assert lead_data["pitch_deck_text"] is None
        return {"some query": {"results": []}}

    monkeypatch.setattr(assess_lead.research, "research_company", _fake_research_company)

    assessment_result = {
        "bucket": "MAYBE",
        "confidence_score": 40,
        "summary": "Website-only assessment.",
        "positive_signals": [],
        "red_flags": [],
        "data_gaps": ["no pitch deck provided"],
        "scoring_breakdown": {},
        "draft_subject": None,
        "draft_body": None,
        "draft_type": None,
        "research_sources": [],
        "precedents_cited": [],
    }

    def _fake_assess(lead_data, research_data, **kwargs):
        captured_research_data.update(research_data)
        return assessment_result

    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _fake_assess)

    import app.services.feedback_patterns as feedback_patterns

    async def _fake_exemplars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _fake_exemplars)

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result["bucket"] == "MAYBE"
    assert lead.status == "assessed"
    assert captured_research_data.get("company_website_content") == scraped_text
    cards = [obj for obj in session.added if isinstance(obj, AssessmentCard)]
    assert len(cards) == 1
    assert cards[0].assessed_without_deck is True


def test_gate_scores_deckless_lead_with_substantial_description_instead_of_parking(monkeypatch):
    """A deckless lead with no website but a substantial description should
    also be assessed, not parked -- description alone can carry it."""
    lead = _fake_lead(
        pitch_deck_text=None,
        website=None,
        description="A well-funded team building proprietary industrial sensor hardware for MENA factories.",
    )
    session = _FakeSession(lead, card=None)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    # No website -> scrape_website_content must not even be called.
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", _boom)
    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {})

    assessment_result = {
        "bucket": "MAYBE",
        "confidence_score": 35,
        "summary": "Description-only assessment.",
        "positive_signals": [],
        "red_flags": [],
        "data_gaps": [],
        "scoring_breakdown": {},
        "draft_subject": None,
        "draft_body": None,
        "draft_type": None,
        "research_sources": [],
        "precedents_cited": [],
    }
    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", lambda *a, **k: assessment_result)

    import app.services.feedback_patterns as feedback_patterns

    async def _fake_exemplars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _fake_exemplars)

    result = asyncio.run(assess_lead._run(str(lead.id)))

    assert result["bucket"] == "MAYBE"
    assert lead.status == "assessed"
    cards = [obj for obj in session.added if isinstance(obj, AssessmentCard)]
    assert cards[0].assessed_without_deck is True


def test_awaiting_deck_lead_leaves_awaiting_deck_once_deck_text_is_attached(monkeypatch):
    """Issue #149, acceptance criterion 2: once the deck sweep attaches
    pitch_deck_text to a lead that was sitting in awaiting_deck and queues
    assess_lead_task (see test_ingest_queues_reassessment_for_awaiting_deck_lead_without_card
    in test_sync_pitch_decks.py), the resulting run must score the lead
    normally and move it off awaiting_deck -- not re-park it."""
    lead = _fake_lead(pitch_deck_text="Deck contents go here " * 50, status="awaiting_deck")
    session = _FakeSession(lead, card=None)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)

    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", _boom)

    assessment_result = {
        "bucket": "MAYBE",
        "confidence_score": 55,
        "summary": "Deck-backed assessment.",
        "positive_signals": [],
        "red_flags": [],
        "data_gaps": [],
        "scoring_breakdown": {},
        "draft_subject": None,
        "draft_body": None,
        "draft_type": None,
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
    assert result["bucket"] == "MAYBE"
    cards = [obj for obj in session.added if isinstance(obj, AssessmentCard)]
    assert cards[0].assessed_without_deck is False


def test_lead_with_deck_text_scores_normally_and_lands_on_assessed(monkeypatch):
    lead = _fake_lead(pitch_deck_text="Deck contents go here " * 50)
    session = _FakeSession(lead, card=None)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)

    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})
    # A deck is present -> the website scrape must be skipped entirely.
    monkeypatch.setattr(assess_lead.research, "scrape_website_content", _boom)

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
    assert lead.assessment_attempts == 0
    assert result["bucket"] == "YES"
    assert result["confidence_score"] == 82
    # A card was created (added) since none existed yet.
    cards = [obj for obj in session.added if isinstance(obj, AssessmentCard)]
    assert len(cards) == 1
    assert cards[0].bucket == "YES"
    assert cards[0].assessed_without_deck is False
