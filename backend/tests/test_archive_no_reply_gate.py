"""
Tests for the mandatory-rating gate on POST /leads/{lead_id}/archive-no-reply.

Mirrors test_assessment_rating_gate.py: none of the rating-gated endpoints may
proceed unless the latest assessment card has an explicit user_rating. The
frontend's one-click "Skip" button is gone (issue #32); this is the
defense-in-depth check that the API can't be used to bypass the mandate
either. A lead with no assessment card at all (never assessed) has nothing to
rate, so that case is allowed through.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.auth import get_current_user

client = TestClient(app)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns queued results for each execute() call in order. archive_no_reply
    issues two queries in sequence: the owned lead, then the latest assessment
    card (if any) — tests queue one value per call."""

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
    )


def _fake_lead(status="assessed"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=None,
        copper_opportunity_id=None,
        company_name="Acme Deep Tech",
        raw_copper_data=None,
        pitch_deck_text=None,
        status=status,
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


def test_archive_no_reply_returns_428_when_unrated(override_auth):
    lead = _fake_lead()
    card = _fake_card(rated=False)
    _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 428
    assert "rate" in response.json()["detail"].lower()
    assert lead.status == "assessed"  # not mutated


def test_archive_no_reply_succeeds_once_rated(override_auth):
    lead = _fake_lead()
    card = _fake_card(rated=True)
    _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"status": "archived", "outcome": "no_reply"}
    assert lead.status == "archived"


def test_archive_no_reply_allowed_when_no_assessment_card_yet(override_auth):
    lead = _fake_lead(status="pending")
    _override_db([lead, None])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"status": "archived", "outcome": "no_reply"}
    assert lead.status == "archived"


def test_archive_no_reply_already_archived_is_a_noop(override_auth):
    lead = _fake_lead(status="archived")
    _override_db([lead])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"status": "already_archived"}
