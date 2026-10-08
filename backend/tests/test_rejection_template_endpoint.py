"""
Tests for POST /assessments/{lead_id}/rejection-template (issue #232): the
template path that sits beside regenerate_draft rather than inside it --
zero LLM calls, validated the same way regenerate-draft's `reasons` are.

Follows the _RecordingSession / _auth_as / _use_db pattern from
test_rejection_reasons.py.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import claude_agent
from app.services.auth import get_current_user

client = TestClient(app)


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


def _fake_lead_row(**overrides):
    base = dict(
        id=uuid.uuid4(),
        owner_email=None,
        company_name="Acme Deep Tech",
        founder_names=["Jane Founder"],
        description="An English-language description.",
        pitch_deck_text="deck text",
        raw_copper_data=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_card_row(bucket="REJECT", **overrides):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    base = dict(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        confidence_score=60,
        summary="not a fit",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject=None,
        draft_body=None,
        draft_type=None,
        draft_bucket=None,
        research_sources=[],
        research_data={},
        assessed_without_deck=False,
        user_override=None,
        user_override_at=None,
        rejection_reasons=None,
        user_rating="up",
        user_rating_at=now,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _call(lead_id, json_body=None):
    kwargs = {"json": json_body} if json_body is not None else {}
    return client.post(f"/api/v1/assessments/{lead_id}/rejection-template", **kwargs)


def test_apply_template_makes_no_llm_call(monkeypatch):
    def _fail_if_called(**_kwargs):
        raise AssertionError("_chat_completion must never be entered on the template send path")

    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_called)

    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id, {"reasons": ["Market size"]})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200


def test_apply_template_persists_subject_body_and_reasons():
    lead = _fake_lead_row(company_name="Acme Deep Tech", founder_names=["Jane Founder"])
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id, {"reasons": ["Market size"], "language": "en"})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["draft_type"] == "rejection"
    assert "Hi Jane," in body["draft_body"]
    assert "Acme Deep Tech" in body["draft_body"]
    assert body["rejection_reasons"] == ["Market size"]
    assert body["rejection_template"] == "MARKET_SIZE"
    assert body["rejection_language"] == "en"
    assert card.draft_bucket == "REJECT"
    assert card.rejection_reasons == ["Market size"]


def test_apply_template_defaults_to_mandate_with_no_reasons():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id)
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["rejection_template"] == "MANDATE"
    assert body["rejection_reasons"] is None


def test_apply_template_honors_user_override_to_reject():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="MAYBE", user_override="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id)
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert card.draft_bucket == "REJECT"


def test_apply_template_rejects_non_reject_bucket():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="YES")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id)
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_apply_template_more_than_three_reasons_is_400_and_writes_nothing():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(
            lead.id,
            {"reasons": ["Market size", "Business Model", "Exit potential", "Technology and IP"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None
    assert card.rejection_reasons is None


def test_apply_template_unknown_reason_is_400_and_writes_nothing():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id, {"reasons": ["Not a real reason"]})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_apply_template_invalid_language_is_400_and_writes_nothing():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id, {"language": "fr"})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_apply_template_founder_only_reason_is_generic_but_recorded():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id, {"reasons": ["Founder(s)"]})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert "founder" not in body["draft_body"].lower()
    assert body["rejection_reasons"] == ["Founder(s)"]
    assert body["rejection_template"] == "MANDATE"


def test_apply_template_arabic_language_override():
    lead = _fake_lead_row(company_name="Acme Deep Tech")
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = _call(lead.id, {"language": "ar"})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["rejection_language"] == "ar"
    assert "رائد فنتشرز" in body["draft_body"]
