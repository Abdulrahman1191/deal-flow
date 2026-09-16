"""
Tests for approve undo (issue #159, extending #153's archive-only undo
mechanism): POST /assessments/{lead_id}/approve snapshots card.approved_at
(None -> set) + lead.status (+ Copper tags) into lead_action_log, and
POST /leads/{lead_id}/undo restores that snapshot. An approve undo refuses
if an email was already sent for it -- un-sending is out of scope.

Same fake-session + TestClient pattern as test_undo_bucket_override.py /
test_undo_archive.py -- no live Postgres.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

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
        self.commits = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        pass


def _fake_lead(status="assessed", copper_id=None, copper_opportunity_id=None, raw_copper_data=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email=None,
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        raw_copper_data=raw_copper_data,
        pitch_deck_text=None,
        status=status,
    )


def _fake_card(bucket="YES", draft_type="meeting_request", user_override=None, approved_at=None,
               lead_id=None, rated=True, sent_at=None):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id or uuid.uuid4(),
        bucket=bucket,
        confidence_score=80,
        summary="promising deep-tech team",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        research_sources=[],
        research_data={},
        assessed_without_deck=False,
        draft_type=draft_type,
        draft_subject="Let's talk" if draft_type else None,
        draft_body="Great news" if draft_type else None,
        draft_bucket=bucket,
        user_override=user_override,
        user_override_at=None,
        user_rating="up" if rated else None,
        user_rating_at=now if rated else None,
        approved_at=approved_at,
        sent_at=sent_at,
        pitch_deck_text=None,
        created_at=now,
    )


def _fake_action(lead_id, card_id, prior_status, prior_tags=None, copper_outbox_id=None,
                  undone_at=None, created_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        card_id=card_id,
        action_type=undo_service.ACTION_APPROVE,
        actor_email="reviewer@raed.vc",
        prior_state={"status": prior_status, "copper_tags": prior_tags or []},
        email_sent=False,
        copper_outbox_id=copper_outbox_id,
        undone_at=undone_at,
        created_at=created_at or datetime.now(timezone.utc),
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


# ---------------------------------------------------------------------------
# 1. approve_assessment snapshots prior state
# ---------------------------------------------------------------------------

def test_approve_snapshots_action_log(override_auth, monkeypatch):
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: "11111111-1111-1111-1111-111111111111")

    card = _fake_card(approved_at=None)
    lead = _fake_lead(status="assessed", copper_id="98765", raw_copper_data={"tags": ["existing-tag"]})

    session = _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/approve")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.approved_at is not None
    assert lead.status == "approved"

    from app.models.lead_action_log import LeadActionLog
    logged = [o for o in session.added if isinstance(o, LeadActionLog)]
    assert len(logged) == 1
    row = logged[0]
    assert row.action_type == undo_service.ACTION_APPROVE
    assert row.card_id == card.id
    assert row.lead_id == lead.id
    assert row.prior_state == {"status": "assessed", "copper_tags": ["existing-tag"]}
    assert row.copper_outbox_id == "11111111-1111-1111-1111-111111111111"


def test_approve_already_approved_is_noop_and_does_not_log(override_auth):
    card = _fake_card(approved_at=datetime.now(timezone.utc))
    lead = _fake_lead()

    session = _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/approve")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"status": "already_approved"}
    from app.models.lead_action_log import LeadActionLog
    assert [o for o in session.added if isinstance(o, LeadActionLog)] == []


# ---------------------------------------------------------------------------
# 2. POST /leads/{lead_id}/undo -- approve
# ---------------------------------------------------------------------------

def test_undo_approve_restores_card_and_lead_status(override_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        copper_writer, "reverse_approve_in_copper",
        lambda copper_id, prior_tags, pending_outbox_id=None: calls.append(
            (copper_id, prior_tags, pending_outbox_id)
        ) or "22222222-2222-2222-2222-222222222222",
    )

    lead = _fake_lead(status="approved", copper_id="98765")
    card = _fake_card(approved_at=datetime.now(timezone.utc), lead_id=lead.id, sent_at=None)
    action = _fake_action(
        lead.id, card.id, prior_status="assessed", prior_tags=["existing-tag"],
        copper_outbox_id="11111111-1111-1111-1111-111111111111",
    )
    override_row = SimpleNamespace(reverted_at=None, created_at=datetime.now(timezone.utc))
    _override_db([lead, action, card, override_row])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "undone"
    assert body["action_type"] == "approve"
    assert body["restored_status"] == "assessed"
    assert body["copper_enqueued"] is True

    assert card.approved_at is None
    assert lead.status == "assessed"
    assert action.undone_at is not None
    assert override_row.reverted_at is not None

    assert calls == [("98765", ["existing-tag"], "11111111-1111-1111-1111-111111111111")]


def test_undo_approve_refuses_when_email_already_sent(override_auth):
    lead = _fake_lead(status="approved")
    card = _fake_card(approved_at=datetime.now(timezone.utc), lead_id=lead.id, sent_at=datetime.now(timezone.utc))
    action = _fake_action(lead.id, card.id, prior_status="assessed")

    _override_db([lead, action, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "un-sending isn't supported" in response.json()["detail"]
    assert card.approved_at is not None  # untouched


def test_undo_approve_refuses_when_status_drifted(override_auth):
    lead = _fake_lead(status="archived")  # not "approved" anymore -- drifted
    card = _fake_card(approved_at=datetime.now(timezone.utc), lead_id=lead.id, sent_at=None)
    action = _fake_action(lead.id, card.id, prior_status="assessed")

    _override_db([lead, action, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "changed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. copper_writer: mark_approved_in_copper / reverse_approve_in_copper
# ---------------------------------------------------------------------------

def test_mark_approved_in_copper_returns_enqueued_row_id(monkeypatch):
    monkeypatch.setattr(copper_writer, "_enqueue", lambda *a, **k: "the-row-id")
    assert copper_writer.mark_approved_in_copper("55555", ["some-tag"]) == "the-row-id"


def test_reverse_approve_in_copper_restores_tags_and_clears_custom_field(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_cf_app_status_id", 555111)

    cancelled = []
    monkeypatch.setattr(copper_writer, "cancel_pending_outbox", lambda oid: cancelled.append(oid))

    enqueued = []
    monkeypatch.setattr(
        copper_writer, "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(
            {"copper_id": copper_id, "endpoint": endpoint, "body": body}
        ) or "new-outbox-id",
    )

    result = copper_writer.reverse_approve_in_copper(
        "55555", ["raed:bucket:yes", "some-real-tag"], pending_outbox_id="old-outbox-id",
    )

    assert result == "new-outbox-id"
    assert cancelled == ["old-outbox-id"]
    assert len(enqueued) == 1
    body = enqueued[0]["body"]
    assert body["tags"] == ["raed:bucket:yes", "some-real-tag"]
    assert body["custom_fields"] == [{"custom_field_definition_id": 555111, "value": ""}]


def test_reverse_approve_in_copper_skips_custom_field_when_unconfigured(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_cf_app_status_id", 0)
    monkeypatch.setattr(copper_writer, "cancel_pending_outbox", lambda oid: None)

    enqueued = []
    monkeypatch.setattr(
        copper_writer, "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(body) or "new-outbox-id",
    )

    copper_writer.reverse_approve_in_copper("55555", [])
    assert "custom_fields" not in enqueued[0]
