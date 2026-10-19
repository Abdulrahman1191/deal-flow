"""
Tests for issue #252: a thumbs on a MAYBE lead decides it -- thumbs-up
promotes to YES (and regenerates a meeting-request draft), thumbs-down
rejects (and regenerates a rejection draft), generalising issue #225's
YES-only thumbs-down auto-reject via the same _apply_rating_transition
helper. See test_rate_down_auto_reject.py for the YES->REJECT and
REJECT-is-terminal coverage this complements.

Same fake-session / TestClient pattern as test_rate_down_auto_reject.py: no
live Postgres, _get_card_and_lead issues one db.execute (joined card+lead
lookup), everything else is db.add()/db.commit() no-ops.
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
from app.services import copper_writer, undo as undo_service
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
    def __init__(self, results):
        self._results = list(results)
        self.added: list = []

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _fake_card(bucket: str, user_override=None, draft_type=None, rejection_reasons=None):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        confidence_score=60,
        summary="interesting but unproven team",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject=None,
        draft_body=None,
        draft_type=draft_type,
        draft_bucket=bucket,
        research_sources=[],
        research_data={},
        assessed_without_deck=False,
        user_override=user_override,
        user_override_at=None,
        user_rating=None,
        user_rating_at=None,
        rejection_reasons=rejection_reasons,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )


def _fake_lead(lead_id=None, copper_id=None, owner_email=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email=owner_email,
        copper_id=copper_id,
        copper_opportunity_id=None,
        company_name="Acme Deep Tech",
        founder_names=["Founder One"],
        description="A deep-tech startup.",
        pitch_deck_text=None,
        raw_copper_data={"recipient_email": "founder@acme.test", "tags": ["raed:bucket:maybe"]},
        status="assessed",
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


def _override_db(results) -> _FakeSession:
    session = _FakeSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


def _fake_regenerate_meeting_request(*_args, **_kwargs):
    return {
        "draft_type": "meeting_request",
        "draft_subject": "Let's talk",
        "draft_body": "We'd love to meet.",
    }


def _fake_regenerate_rejection(*_args, **_kwargs):
    return {
        "draft_type": "rejection",
        "draft_subject": "Thanks for applying",
        "draft_body": "Thanks, but this isn't a fit right now.",
    }


# ---------------------------------------------------------------------------
# 1. thumbs-up on MAYBE promotes to YES
# ---------------------------------------------------------------------------


def test_rate_up_on_maybe_promotes_to_yes_and_regenerates_meeting_request(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_meeting_request)

    events = []

    async def _fake_log_event(db, lead_id, event_type, payload=None):
        events.append((event_type, payload))

    monkeypatch.setattr(assessments_router, "log_event", _fake_log_event)

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.bucket == "YES"
    assert card.user_override == "YES"
    assert card.draft_type == "meeting_request"
    assert card.draft_subject == "Let's talk"
    assert card.draft_body == "We'd love to meet."
    assert card.draft_bucket == "YES"

    assert ("bucket_overridden", {"from": "MAYBE", "to": "YES", "source": "rate_up_auto"}) in events


def test_rate_up_on_maybe_mirrors_bucket_tag_to_copper(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_meeting_request)

    calls = []
    monkeypatch.setattr(
        copper_writer, "set_bucket_tag",
        lambda copper_id, new_bucket, existing_tags: calls.append((copper_id, new_bucket, existing_tags)),
    )

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id, copper_id="copper-123")
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [("copper-123", "YES", ["raed:bucket:maybe"])]


# ---------------------------------------------------------------------------
# 2. thumbs-down on MAYBE rejects
# ---------------------------------------------------------------------------


def test_rate_down_on_maybe_rejects_and_regenerates_rejection_draft(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.bucket == "REJECT"
    assert card.user_override == "REJECT"
    assert card.draft_type == "rejection"
    assert card.draft_subject == "Thanks for applying"
    assert card.draft_body == "Thanks, but this isn't a fit right now."
    assert card.draft_bucket == "REJECT"


def test_rate_down_on_maybe_with_canonical_reason_stores_rejection_reasons(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{card.lead_id}/rate",
            json={"rating": "down", "rejection_reason": "Lack of traction"},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.rejection_reasons == ["Lack of traction"]


# ---------------------------------------------------------------------------
# 3. training row carries the destination bucket and the new trigger names
# ---------------------------------------------------------------------------


def test_rate_up_training_row_records_promote_trigger(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_meeting_request)

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id)
    session = _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    from app.models.override import AssessmentOverride
    rows = [o for o in session.added if isinstance(o, AssessmentOverride)]
    assert len(rows) == 1
    row = rows[0]
    assert row.ai_bucket == "MAYBE"
    assert row.human_bucket == "YES"
    assert row.trigger == "rate_up_auto_promote"


def test_rate_down_training_row_records_reject_trigger(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id)
    session = _override_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{card.lead_id}/rate",
            json={"rating": "down", "reason_tags": ["small market"], "reason": "too niche"},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    from app.models.override import AssessmentOverride
    rows = [o for o in session.added if isinstance(o, AssessmentOverride)]
    assert len(rows) == 1
    row = rows[0]
    assert row.ai_bucket == "MAYBE"
    assert row.human_bucket == "REJECT"
    assert row.trigger == "rate_down_auto_reject"
    assert row.human_reason_tags == ["small market"]
    assert row.human_reason == "too niche"


# ---------------------------------------------------------------------------
# 4. draft-regeneration failure nulls the draft rather than leaving a
# MAYBE-era or meeting-request draft on a rejected/promoted lead
# ---------------------------------------------------------------------------


def test_promote_draft_regen_failure_nulls_draft(override_auth, monkeypatch):
    def _always_fail(*_a, **_k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _always_fail)
    monkeypatch.setattr(assessments_router, "_DRAFT_REGEN_MAX_ATTEMPTS", 1)

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.bucket == "YES"
    assert card.user_override == "YES"
    assert card.draft_type is None
    assert card.draft_subject is None
    assert card.draft_body is None
    assert card.draft_bucket is None


def test_reject_draft_regen_failure_nulls_draft(override_auth, monkeypatch):
    def _always_fail(*_a, **_k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _always_fail)
    monkeypatch.setattr(assessments_router, "_DRAFT_REGEN_MAX_ATTEMPTS", 1)

    card = _fake_card(bucket="MAYBE")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.bucket == "REJECT"
    assert card.user_override == "REJECT"
    assert card.draft_type is None
    assert card.draft_subject is None
    assert card.draft_body is None
    assert card.draft_bucket is None


# ---------------------------------------------------------------------------
# 5. the transition is undoable by the same path as an override
# ---------------------------------------------------------------------------


def test_rate_up_on_maybe_is_undoable_via_bucket_override_action(override_auth, monkeypatch):
    """Promoting MAYBE->YES snapshots a bucket_override action log row the
    same way issue #225's auto-reject does, so POST /leads/{id}/undo can
    restore it without any new undo machinery."""
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_meeting_request)

    card = _fake_card(bucket="MAYBE", draft_type=None)
    lead = _fake_lead(lead_id=card.lead_id)
    session = _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_db_override()

    assert response.status_code == 200

    from app.models.lead_action_log import LeadActionLog
    rows = [o for o in session.added if isinstance(o, LeadActionLog)]
    assert len(rows) == 1
    action = rows[0]
    assert action.action_type == undo_service.ACTION_BUCKET_OVERRIDE
    assert action.prior_state["bucket"] == "MAYBE"
    assert action.prior_state["new_bucket"] == "YES"
