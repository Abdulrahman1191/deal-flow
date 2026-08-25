"""
Tests for the issue #150 safety net: a lead moved REJECT->YES (or vice versa)
must never send a draft written for its old bucket.

Covers:
  1. Overriding REJECT->YES regenerates the draft into a meeting request and
     records draft_bucket='YES'.
  2. If every regen attempt fails, the draft is nulled out (marked stale)
     rather than left as the old bucket's draft, the response says so via
     draft_regen_failed, and approve/send are then refused with 409.
  3. approve/send/mark-sent all refuse (409) whenever draft_type contradicts
     the effective bucket, even without going through /override -- e.g. a
     card that was hand-edited or left over from a previous state.

Uses the same fake-session / TestClient pattern as
test_assessment_rating_gate.py: no live Postgres needed. `_get_card_and_lead`
issues exactly one `db.execute` (the joined card+lead lookup); leads here have
owner_email=None and copper_id=None so no further `db.execute`/Copper calls
happen along the way.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.routers.assessments as assessments_router
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
    """Returns queued results for each execute() call in order, mirroring
    test_assessment_rating_gate.py. add()/commit()/refresh() are no-ops."""

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


def _fake_card(bucket: str, draft_type, rated: bool = True, user_override=None):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        confidence_score=80,
        summary="promising deep-tech team",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject="Some subject" if draft_type else None,
        draft_body="Some body" if draft_type else None,
        draft_type=draft_type,
        draft_bucket=bucket,
        research_sources=[],
        research_data={},
        assessed_without_deck=False,
        user_override=user_override,
        user_override_at=None,
        user_rating="up" if rated else None,
        user_rating_at=now if rated else None,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )


def _fake_lead(lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email=None,
        copper_id=None,
        copper_opportunity_id=None,
        company_name="Acme Deep Tech",
        founder_names=["Founder One"],
        description="A deep-tech startup.",
        pitch_deck_text=None,
        raw_copper_data={"recipient_email": "founder@acme.test"},
        status="pending",
    )


@pytest.fixture
def override_auth():
    async def _fake_current_user():
        return SimpleNamespace(email="reviewer@raed.vc", is_active=True)

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


# ---------- override regenerates the draft to match the new bucket ----------


def test_override_reject_to_yes_regenerates_draft_and_sets_draft_bucket(override_auth, monkeypatch):
    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id)

    def _fake_regenerate(*_args, **_kwargs):
        return {
            "draft_type": "meeting_request",
            "draft_subject": "Let's talk",
            "draft_body": "We'd love to meet.",
        }

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["draft_type"] == "meeting_request"
    assert body["draft_bucket"] == "YES"
    assert body["draft_regen_failed"] is False

    assert card.draft_type == "meeting_request"
    assert card.draft_subject == "Let's talk"
    assert card.draft_bucket == "YES"


# ---------- regen failure marks the draft stale and blocks send ----------


def test_override_regen_failure_marks_draft_stale_and_surfaces_flag(override_auth, monkeypatch):
    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id)

    call_count = {"n": 0}

    def _always_fails(*_args, **_kwargs):
        call_count["n"] += 1
        raise RuntimeError("LLM hiccup")

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _always_fails)

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    # A regen failure must not fail the override itself -- the bucket change
    # still takes effect -- but the draft must be nulled out rather than left
    # as the old REJECT-bucket rejection draft, and the caller must be told.
    assert response.status_code == 200
    body = response.json()
    assert body["draft_regen_failed"] is True
    assert body["draft_type"] is None
    assert body["draft_subject"] is None
    assert body["draft_body"] is None
    assert body["draft_bucket"] is None
    assert card.user_override == "YES"

    # A retry actually happened -- not just one shot.
    assert call_count["n"] == assessments_router._DRAFT_REGEN_MAX_ATTEMPTS

    # Now the stale (nulled) draft must refuse to send rather than silently
    # going out -- this is the actual bug from issue #150.
    _override_db([(card, lead)])
    try:
        send_response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert send_response.status_code == 409
    assert "stale" in send_response.json()["detail"].lower()


# ---------- send-time guard: draft_type vs effective bucket ----------


@pytest.mark.parametrize("path", ["approve", "send", "mark-sent"])
def test_mismatched_draft_type_is_refused(override_auth, monkeypatch, path):
    """A card whose bucket is YES but whose draft is still a rejection (e.g.
    left over from before a bucket flip) must never be approved/sent/marked
    sent -- regardless of how it got into that state."""
    monkeypatch.setattr(email_sender, "is_configured", lambda: True)
    sent_calls = []
    monkeypatch.setattr(
        email_sender, "send_email", lambda *a, **kw: sent_calls.append((a, kw))
    )

    card = _fake_card(bucket="YES", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id)

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/{path}")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()
    assert sent_calls == []
    assert card.sent_at is None
    assert card.approved_at is None


@pytest.mark.parametrize("path", ["approve", "send", "mark-sent"])
def test_matching_draft_type_is_allowed(override_auth, monkeypatch, path):
    monkeypatch.setattr(email_sender, "is_configured", lambda: True)
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **kw: None)

    card = _fake_card(bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(lead_id=card.lead_id)

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/{path}")
    finally:
        _clear_db_override()

    assert response.status_code == 200


def test_regenerate_draft_endpoint_sets_draft_bucket(override_auth, monkeypatch):
    """The manual "Regenerate" recovery action must also record draft_bucket
    (issue #150), same as /override, so a subsequent stale check has an
    accurate record of which bucket the draft was last written for."""
    card = _fake_card(bucket="YES", draft_type=None, user_override=None)
    card.draft_subject = None
    card.draft_body = None
    card.draft_bucket = None

    def _fake_regenerate(*_args, **_kwargs):
        return {"draft_type": "meeting_request", "draft_subject": "Let's talk", "draft_body": "Great news."}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/regenerate-draft")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["draft_bucket"] == "YES"
    assert card.draft_bucket == "YES"
