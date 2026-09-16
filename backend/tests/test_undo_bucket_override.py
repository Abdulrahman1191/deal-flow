"""
Tests for bucket-override undo (issue #159, extending #153's archive-only
undo mechanism): POST /assessments/{lead_id}/override snapshots the card's
bucket/user_override/draft fields (+ Copper tags) into lead_action_log
before overwriting them, and POST /leads/{lead_id}/undo restores that
snapshot -- including the draft, since a bucket override always regenerates
or nulls it out.

Same fake-session + TestClient pattern as test_undo_archive.py /
test_stale_draft_guard.py -- no live Postgres.
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
        self.commits = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        pass


def _fake_lead(status="approved", copper_id=None, copper_opportunity_id=None, raw_copper_data=None):
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


def _fake_card(bucket, draft_type, user_override=None, draft_subject="s", draft_body="b", draft_bucket=None,
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
        draft_subject=draft_subject if draft_type else None,
        draft_body=draft_body if draft_type else None,
        draft_bucket=draft_bucket if draft_bucket is not None else bucket,
        user_override=user_override,
        user_override_at=None,
        user_rating="up" if rated else None,
        user_rating_at=now if rated else None,
        approved_at=None,
        sent_at=sent_at,
        pitch_deck_text=None,
        created_at=now,
    )


def _fake_action(lead_id, card_id, prior_bucket, prior_user_override, prior_draft_type,
                  prior_draft_subject, prior_draft_body, prior_draft_bucket, prior_tags,
                  resulting_bucket, copper_outbox_id=None, undone_at=None, created_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        card_id=card_id,
        action_type=undo_service.ACTION_BUCKET_OVERRIDE,
        actor_email="reviewer@raed.vc",
        prior_state={
            "bucket": prior_bucket,
            "user_override": prior_user_override,
            "draft_type": prior_draft_type,
            "draft_subject": prior_draft_subject,
            "draft_body": prior_draft_body,
            "draft_bucket": prior_draft_bucket,
            "copper_tags": prior_tags or [],
            "resulting_bucket": resulting_bucket,
        },
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
# 1. override_bucket snapshots prior state (incl. draft fields)
# ---------------------------------------------------------------------------

def test_override_bucket_snapshots_action_log(override_auth, monkeypatch):
    monkeypatch.setattr(
        assessments_router.claude_agent, "regenerate_draft",
        lambda *a, **k: {"draft_type": "meeting_request", "draft_subject": "Let's talk", "draft_body": "Great news."},
    )
    monkeypatch.setattr(copper_writer, "set_bucket_tag", lambda *a, **k: "11111111-1111-1111-1111-111111111111")

    card = _fake_card(bucket="REJECT", draft_type="rejection", draft_subject="Sorry", draft_body="Not a fit",
                      draft_bucket="REJECT")
    lead = _fake_lead(copper_id="98765", raw_copper_data={"tags": ["existing-tag"]})

    session = _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200

    from app.models.lead_action_log import LeadActionLog
    logged = [o for o in session.added if isinstance(o, LeadActionLog)]
    assert len(logged) == 1
    row = logged[0]
    assert row.action_type == undo_service.ACTION_BUCKET_OVERRIDE
    assert row.card_id == card.id
    assert row.lead_id == lead.id
    assert row.prior_state["bucket"] == "REJECT"
    assert row.prior_state["user_override"] is None
    assert row.prior_state["draft_type"] == "rejection"
    assert row.prior_state["draft_subject"] == "Sorry"
    assert row.prior_state["draft_body"] == "Not a fit"
    assert row.prior_state["draft_bucket"] == "REJECT"
    assert row.prior_state["copper_tags"] == ["existing-tag"]
    assert row.prior_state["resulting_bucket"] == "YES"
    assert row.copper_outbox_id == "11111111-1111-1111-1111-111111111111"


def test_override_bucket_noop_does_not_log(override_auth):
    card = _fake_card(bucket="YES", draft_type="meeting_request", user_override=None)
    lead = _fake_lead()

    session = _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    from app.models.lead_action_log import LeadActionLog
    assert [o for o in session.added if isinstance(o, LeadActionLog)] == []


# ---------------------------------------------------------------------------
# 2. POST /leads/{lead_id}/undo -- bucket_override
# ---------------------------------------------------------------------------

def test_undo_bucket_override_restores_card_and_reverses_copper_tag(override_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        copper_writer, "reverse_bucket_tag",
        lambda copper_id, prior_tags, pending_outbox_id=None: calls.append(
            (copper_id, prior_tags, pending_outbox_id)
        ) or "22222222-2222-2222-2222-222222222222",
    )

    lead = _fake_lead(status="approved", copper_id="98765")
    card = _fake_card(bucket="YES", draft_type="meeting_request", user_override="YES", lead_id=lead.id,
                      draft_subject="Let's talk", draft_body="Great news", draft_bucket="YES")
    action = _fake_action(
        lead.id, card.id, prior_bucket="REJECT", prior_user_override=None,
        prior_draft_type="rejection", prior_draft_subject="Sorry", prior_draft_body="Not a fit",
        prior_draft_bucket="REJECT", prior_tags=["existing-tag"], resulting_bucket="YES",
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
    assert body["action_type"] == "bucket_override"
    assert body["restored_bucket"] == "REJECT"
    assert body["copper_enqueued"] is True

    assert card.bucket == "REJECT"
    assert card.user_override is None
    assert card.draft_type == "rejection"
    assert card.draft_subject == "Sorry"
    assert card.draft_body == "Not a fit"
    assert card.draft_bucket == "REJECT"
    assert action.undone_at is not None
    assert override_row.reverted_at is not None
    # lead.status is untouched by a bucket-override undo -- it never changed it.
    assert lead.status == "approved"

    assert calls == [("98765", ["existing-tag"], "11111111-1111-1111-1111-111111111111")]


def test_undo_bucket_override_refuses_when_bucket_drifted(override_auth):
    lead = _fake_lead(status="approved")
    card = _fake_card(bucket="MAYBE", draft_type=None, user_override="MAYBE", lead_id=lead.id)  # re-overridden since
    action = _fake_action(
        lead.id, card.id, prior_bucket="REJECT", prior_user_override=None,
        prior_draft_type="rejection", prior_draft_subject="s", prior_draft_body="b",
        prior_draft_bucket="REJECT", prior_tags=[], resulting_bucket="YES",
    )
    _override_db([lead, action, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "changed" in response.json()["detail"].lower()
    assert card.bucket == "MAYBE"  # untouched


def test_undo_bucket_override_refuses_when_card_no_longer_exists(override_auth):
    lead = _fake_lead(status="approved")
    action = _fake_action(
        lead.id, uuid.uuid4(), prior_bucket="REJECT", prior_user_override=None,
        prior_draft_type="rejection", prior_draft_subject="s", prior_draft_body="b",
        prior_draft_bucket="REJECT", prior_tags=[], resulting_bucket="YES",
    )
    _override_db([lead, action, None])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "no longer exists" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. copper_writer: set_bucket_tag / reverse_bucket_tag
# ---------------------------------------------------------------------------

def test_set_bucket_tag_returns_enqueued_row_id(monkeypatch):
    monkeypatch.setattr(copper_writer, "_enqueue", lambda *a, **k: "the-row-id")
    assert copper_writer.set_bucket_tag("55555", "YES", ["some-tag"]) == "the-row-id"


def test_reverse_bucket_tag_restores_exact_prior_tags_and_cancels_pending(monkeypatch):
    cancelled = []
    monkeypatch.setattr(copper_writer, "cancel_pending_outbox", lambda oid: cancelled.append(oid))

    enqueued = []
    monkeypatch.setattr(
        copper_writer, "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(
            {"copper_id": copper_id, "endpoint": endpoint, "body": body}
        ) or "new-outbox-id",
    )

    result = copper_writer.reverse_bucket_tag(
        "55555", ["raed:bucket:reject", "some-real-tag"], pending_outbox_id="old-outbox-id",
    )

    assert result == "new-outbox-id"
    assert cancelled == ["old-outbox-id"]
    assert len(enqueued) == 1
    assert enqueued[0]["body"] == {"tags": ["raed:bucket:reject", "some-real-tag"]}
    # No status_id -- a bucket override never changes the Copper lead's status.
    assert "status_id" not in enqueued[0]["body"]
