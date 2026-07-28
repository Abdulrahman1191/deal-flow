"""
Generated outreach drafts must carry the lead OWNER's Calendly link + name,
not a single hardcoded associate's (issue #84). Covers three layers:

  1. claude_agent.assess_lead / regenerate_draft — the actual prompt +
     substitution logic, exercised against a fake DeepSeek client (mirrors
     the LLM-mocking pattern in test_pitch_deck.py) so no live API key is
     needed. The fake echoes back whatever Calendly link it was given in the
     prompt, so these tests catch a regression where the owner's link never
     makes it into the request sent to the model.
  2. app.tasks.assess_lead._run — the caller must look up the lead's owner
     User (by lead.owner_email) and pass calendly_url/full_name through.
     Exercised with the same _FakeSession approach as
     test_assess_lead_awaiting_deck.py, so no live Postgres is needed.
  3. POST /assessments/{id}/regenerate-draft — same owner lookup, exercised
     with the _RecordingSession pattern from test_multiuser_access.py.
"""
from __future__ import annotations
import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.models.user import User
from app.services import claude_agent
from app.services.auth import get_current_user
from app.tasks import assess_lead

client = TestClient(app)


# ---------- fake DeepSeek client (layer 1) ----------


class _EchoingCompletions:
    """Stands in for OpenAI's chat.completions -- echoes back whichever
    Calendly link appears in the prompt it was given, and always emits the
    "[Associate Name]" placeholder in the sign-off, matching how the real
    model is instructed to behave (see claude_agent._PLACEHOLDER)."""

    def create(self, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        match = re.search(r"https://calendly\.com/\S+?(?=[.\s]|$)", prompt)
        calendly_url = match.group(0) if match else "MISSING_CALENDLY"
        payload = {
            "summary": "Deep tech, MENA.",
            "bucket": "YES",
            "confidence_score": 85,
            "scoring_breakdown": {},
            "positive_signals": [],
            "red_flags": [],
            "data_gaps": [],
            "research_sources": [],
            "draft_type": "meeting_request",
            "draft_subject": "Thanks for applying to Raed Ventures",
            "draft_body": (
                f"Hi there,\n\nWould you like to book a short call? {calendly_url}\n\n"
                "Best,\n[Associate Name], Raed Ventures"
            ),
        }
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_EchoingCompletions())


def _install_fake_llm(monkeypatch):
    monkeypatch.setattr(claude_agent, "_get_client", lambda: _FakeClient())


# ---------- layer 1: claude_agent.assess_lead / regenerate_draft ----------


def test_regenerate_draft_uses_owner_calendly_and_name(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "YES",
        "promising team",
        owner_calendly="https://calendly.com/waleed-raed/pl",
        owner_name="Waleed",
    )
    assert "https://calendly.com/waleed-raed/pl" in result["draft_body"]
    assert "Waleed, Raed Ventures" in result["draft_body"]
    assert "[Associate Name]" not in result["draft_body"]


def test_regenerate_draft_falls_back_when_owner_has_neither(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "YES",
        "promising team",
        owner_calendly=None,
        owner_name=None,
    )
    assert claude_agent.DEFAULT_CALENDLY_URL in result["draft_body"]
    from app.config import settings

    assert f"{settings.associate_name}, Raed Ventures" in result["draft_body"]


def test_assess_lead_uses_owner_calendly_and_name(monkeypatch):
    _install_fake_llm(monkeypatch)
    lead_data = {
        "company_name": "Acme Deep Tech",
        "website": "https://acme.test",
        "description": "A deep-tech startup.",
        "stage": "seed",
        "region": "MENA",
        "founder_names": ["Founder One"],
        "linkedin_urls": [],
        "pitch_deck_text": "deck text",
    }
    result = claude_agent.assess_lead(
        lead_data,
        research_data={},
        owner_calendly="https://calendly.com/udayrvc/30min",
        owner_name="Uday",
    )
    assert "https://calendly.com/udayrvc/30min" in result["draft_body"]
    assert "Uday, Raed Ventures" in result["draft_body"]


def test_assess_lead_falls_back_when_owner_has_neither(monkeypatch):
    _install_fake_llm(monkeypatch)
    lead_data = {
        "company_name": "Acme Deep Tech",
        "website": "https://acme.test",
        "description": "A deep-tech startup.",
        "stage": "seed",
        "region": "MENA",
        "founder_names": ["Founder One"],
        "linkedin_urls": [],
        "pitch_deck_text": "deck text",
    }
    result = claude_agent.assess_lead(lead_data, research_data={})
    assert claude_agent.DEFAULT_CALENDLY_URL in result["draft_body"]
    from app.config import settings

    assert f"{settings.associate_name}, Raed Ventures" in result["draft_body"]


# ---------- layer 2: app.tasks.assess_lead._run loads the owner ----------


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeTaskSession:
    """Mirrors test_assess_lead_awaiting_deck.py's _FakeSession: dispatches
    execute() by the query's target entity."""

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


def _fake_lead(owner_email=None):
    return SimpleNamespace(
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
        owner_email=owner_email,
    )


def _fake_user(email, calendly_url=None, full_name=None):
    return SimpleNamespace(email=email, calendly_url=calendly_url, full_name=full_name)


def test_assess_lead_task_passes_owner_fields_to_claude_agent(monkeypatch):
    lead = _fake_lead(owner_email="waleed@raed.vc")
    owner = _fake_user("waleed@raed.vc", calendly_url="https://calendly.com/waleed-raed/pl", full_name="Waleed")
    session = _FakeTaskSession(lead, owner=owner)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})

    captured = {}

    def _fake_assess(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "bucket": "YES",
            "confidence_score": 82,
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

    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _fake_assess)

    import app.services.feedback_patterns as feedback_patterns

    async def _fake_exemplars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _fake_exemplars)

    asyncio.run(assess_lead._run(str(lead.id)))

    assert captured["owner_calendly"] == "https://calendly.com/waleed-raed/pl"
    assert captured["owner_name"] == "Waleed"


def test_assess_lead_task_passes_none_when_owner_not_provisioned(monkeypatch):
    lead = _fake_lead(owner_email="brandnew@raed.vc")
    session = _FakeTaskSession(lead, owner=None)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})

    captured = {}

    def _fake_assess(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "bucket": "YES",
            "confidence_score": 82,
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

    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _fake_assess)

    import app.services.feedback_patterns as feedback_patterns

    async def _fake_exemplars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _fake_exemplars)

    asyncio.run(assess_lead._run(str(lead.id)))

    assert captured["owner_calendly"] is None
    assert captured["owner_name"] is None


# ---------- layer 3: POST /assessments/{id}/regenerate-draft ----------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(results):
    session = _RecordingSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def _fake_lead_row(owner_email):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email=owner_email,
        company_name="Acme Deep Tech",
        founder_names=["Founder One"],
        description="An English-language description.",
        pitch_deck_text="deck text",
    )


def _fake_card_row(bucket="YES"):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        confidence_score=80,
        summary="promising team",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject=None,
        draft_body=None,
        draft_type=None,
        research_sources=[],
        research_data={},
        user_override=None,
        user_override_at=None,
        user_rating="up",
        user_rating_at=now,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )


def test_regenerate_draft_endpoint_passes_owner_fields(monkeypatch):
    lead = _fake_lead_row("waleed@raed.vc")
    card = _fake_card_row()
    owner = _fake_user("waleed@raed.vc", calendly_url="https://calendly.com/waleed-raed/pl", full_name="Waleed")

    captured = {}

    def _fake_regenerate(*_args, **kwargs):
        captured.update(kwargs)
        return {"draft_type": "meeting_request", "draft_subject": "Hi", "draft_body": "Body"}

    import app.routers.assessments as assessments_router

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    _auth_as("waleed@raed.vc")
    _use_db([(card, lead), owner])
    try:
        response = client.post(f"/api/v1/assessments/{lead.id}/regenerate-draft")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert captured["owner_calendly"] == "https://calendly.com/waleed-raed/pl"
    assert captured["owner_name"] == "Waleed"


def test_regenerate_draft_endpoint_falls_back_when_owner_not_provisioned(monkeypatch):
    lead = _fake_lead_row("brandnew@raed.vc")
    card = _fake_card_row()

    captured = {}

    def _fake_regenerate(*_args, **kwargs):
        captured.update(kwargs)
        return {"draft_type": "meeting_request", "draft_subject": "Hi", "draft_body": "Body"}

    import app.routers.assessments as assessments_router

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    _auth_as("brandnew@raed.vc")
    _use_db([(card, lead), None])
    try:
        response = client.post(f"/api/v1/assessments/{lead.id}/regenerate-draft")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert captured["owner_calendly"] is None
    assert captured["owner_name"] is None
