"""
Tests for the mandatory-rating gate on /approve, /send, and /mark-sent.

Enforced learning: none of the three endpoints may proceed unless the latest
assessment card has an explicit user_rating ("up"/"down") — a bucket override
alone does not satisfy it. Exercised with fastapi.testclient.TestClient against
app.database.get_db and app.services.auth.get_current_user stubs, mirroring the
pattern in test_csv_export.py — no live Postgres needed.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import email_sender
from app.services.auth import get_current_user

client = TestClient(app)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


class _FakeSession:
    """Returns queued results for each execute() call in order. The router
    fetches the card and its owning lead together in a single joined query
    (a `(card, lead)` tuple), so tests queue one tuple per assessment lookup;
    add()/commit()/refresh() are no-ops since capture_override/log_event side
    effects aren't under test here."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _fake_card(rated: bool, bucket: str = "YES"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        user_override=None,
        user_rating="up" if rated else None,
        confidence_score=80,
        summary="promising deep-tech team",
        scoring_breakdown={},
        research_data={},
        draft_type="meeting_request" if bucket == "YES" else "rejection",
        draft_subject="Let's talk",
        draft_body="Hi there",
        approved_at=None,
        sent_at=None,
    )


def _fake_lead():
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=None,
        copper_opportunity_id=None,
        company_name="Acme Deep Tech",
        founder_names=["Founder One"],
        raw_copper_data={"recipient_email": "founder@acme.test"},
        pitch_deck_text=None,
        status="pending",
    )


async def _fake_current_user():
    return SimpleNamespace(email="reviewer@raed.vc", is_active=True)


@pytest.fixture
def override_auth():
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _override_db(results):
    async def _fake_get_db():
        yield _FakeSession(results)

    app.dependency_overrides[get_db] = _fake_get_db


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize(
    "path",
    ["approve", "send", "mark-sent"],
)
def test_unrated_card_returns_428(override_auth, path):
    card = _fake_card(rated=False)
    lead = _fake_lead()
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/{path}")
    finally:
        _clear_db_override()

    assert response.status_code == 428
    assert "rate" in response.json()["detail"].lower()


def test_approve_succeeds_once_rated(override_auth):
    card = _fake_card(rated=True)
    lead = _fake_lead()
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/approve")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert card.approved_at is not None


def test_mark_sent_succeeds_once_rated(override_auth):
    card = _fake_card(rated=True)
    lead = _fake_lead()
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/mark-sent")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert card.sent_at is not None


def test_send_succeeds_once_rated(override_auth, monkeypatch):
    monkeypatch.setattr(email_sender, "is_configured", lambda: True)
    sent_calls = []
    monkeypatch.setattr(
        email_sender,
        "send_email",
        lambda to, subject, body: sent_calls.append((to, subject, body)),
    )

    card = _fake_card(rated=True)
    lead = _fake_lead()
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert sent_calls == [("founder@acme.test", "Let's talk", "Hi there")]
    assert card.approved_at is not None
    assert card.sent_at is not None
