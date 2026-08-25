"""
Tests for issue #157: correcting the Copper disposition when a lead moves out
of REJECT after already being written back as Unqualified.

Uses the same fake-session / TestClient pattern as test_stale_draft_guard.py:
no live Postgres needed. `_get_card_and_lead` issues exactly one `db.execute`
(the joined card+lead lookup).
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

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _fake_card(bucket: str, draft_type, user_override=None):
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
        user_rating="up",
        user_rating_at=now,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )


def _fake_lead(lead_id=None, copper_id="copper-123", copper_unqualified_at=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email=None,
        copper_id=copper_id,
        copper_opportunity_id=None,
        company_name="Acme Deep Tech",
        founder_names=["Founder One"],
        description="A deep-tech startup.",
        pitch_deck_text=None,
        raw_copper_data={"recipient_email": "founder@acme.test", "tags": ["raed:archived", "raed:bucket:reject"]},
        status="archived",
        copper_unqualified_at=copper_unqualified_at,
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


def _fake_regenerate(*_args, **_kwargs):
    return {
        "draft_type": "meeting_request",
        "draft_subject": "Let's talk",
        "draft_body": "We'd love to meet.",
    }


def test_reject_to_yes_on_previously_unqualified_lead_restores_and_clears_fields(override_auth, monkeypatch):
    """The core issue #157 scenario: a lead was sent as a rejection (Copper
    written back as Unqualified), then the partner overrides it REJECT->YES.
    The Copper write-back must reopen the status and clear the unqualification
    reason/detail custom fields in the same call, not just swap the bucket tag."""
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    card = _fake_card(bucket="REJECT", draft_type="rejection")
    unqualified_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    lead = _fake_lead(lead_id=card.lead_id, copper_unqualified_at=unqualified_at)

    calls = []

    def _fake_restore(copper_id, new_bucket, existing_tags):
        calls.append({"copper_id": copper_id, "new_bucket": new_bucket, "existing_tags": existing_tags})

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("set_bucket_tag should not be called when a correction is needed")

    monkeypatch.setattr(assessments_router.copper_writer, "restore_from_unqualified", _fake_restore)
    monkeypatch.setattr(assessments_router.copper_writer, "set_bucket_tag", _fail_if_called)

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["copper_id"] == "copper-123"
    assert calls[0]["new_bucket"] == "YES"

    # Idempotency: the flag is cleared so a later override doesn't re-fire it.
    assert lead.copper_unqualified_at is None


def test_reject_to_maybe_on_previously_unqualified_lead_also_corrects(override_auth, monkeypatch):
    """MAYBE is an equally valid destination per the acceptance criteria, not
    just YES."""
    def _fake_regen_maybe(*_a, **_kw):
        return {"draft_type": None, "draft_subject": None, "draft_body": None}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regen_maybe)

    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id, copper_unqualified_at=datetime(2026, 8, 1, tzinfo=timezone.utc))

    calls = []
    monkeypatch.setattr(
        assessments_router.copper_writer, "restore_from_unqualified",
        lambda copper_id, new_bucket, existing_tags: calls.append(new_bucket),
    )
    monkeypatch.setattr(
        assessments_router.copper_writer, "set_bucket_tag",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("set_bucket_tag should not be called")),
    )

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "MAYBE"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == ["MAYBE"]
    assert lead.copper_unqualified_at is None


def test_override_without_prior_unqualified_write_is_a_plain_tag_swap(override_auth, monkeypatch):
    """A lead overridden REJECT->YES before ever being sent/archived (no
    Unqualified write happened) must not trigger the correction path -- just
    the existing bucket-tag swap. Guards against sending pointless writes."""
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id, copper_unqualified_at=None)

    restore_calls = []
    tag_calls = []
    monkeypatch.setattr(
        assessments_router.copper_writer, "restore_from_unqualified",
        lambda *a, **kw: restore_calls.append(a),
    )
    monkeypatch.setattr(
        assessments_router.copper_writer, "set_bucket_tag",
        lambda copper_id, new_bucket, existing_tags: tag_calls.append(new_bucket),
    )

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert restore_calls == []
    assert tag_calls == ["YES"]


def test_yes_to_maybe_after_correction_does_not_refire(override_auth, monkeypatch):
    """Once the flag has been cleared by a prior REJECT->YES correction, a
    subsequent YES->MAYBE (or any further override not originating from
    REJECT) must not call restore_from_unqualified again."""
    def _fake_regen_maybe(*_a, **_kw):
        return {"draft_type": None, "draft_subject": None, "draft_body": None}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regen_maybe)

    card = _fake_card(bucket="YES", draft_type="meeting_request", user_override="YES")
    lead = _fake_lead(lead_id=card.lead_id, copper_unqualified_at=None)

    restore_calls = []
    tag_calls = []
    monkeypatch.setattr(
        assessments_router.copper_writer, "restore_from_unqualified",
        lambda *a, **kw: restore_calls.append(a),
    )
    monkeypatch.setattr(
        assessments_router.copper_writer, "set_bucket_tag",
        lambda copper_id, new_bucket, existing_tags: tag_calls.append(new_bucket),
    )

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "MAYBE"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert restore_calls == []
    assert tag_calls == ["MAYBE"]
