"""
Tests for the archive-only undo mechanism (issue #153, narrowed scope):
  - archive_no_reply / the rejection-send archive snapshot prior state into
    lead_action_log before overwriting it.
  - POST /leads/{lead_id}/undo restores that snapshot, reverses the Copper
    write-back through the outbox, and marks the related assessment_overrides
    row reverted so it drops out of feedback_patterns exemplar retrieval.

Same fake-session + TestClient pattern as test_archive_no_reply_gate.py /
test_copper_writebacks.py -- no live Postgres. Copper itself is mocked by
stubbing the copper_writer functions the router/service calls.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import copper_writer, feedback_patterns, undo as undo_service
from app.services.auth import get_current_user

client = TestClient(app)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns queued results for each execute() call in order, and records
    everything passed to add() so tests can inspect the rows the router/
    service constructed (mirrors test_backfill_awaiting_deck.py's pattern)."""

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
        owner_email="reviewer@raed.vc",
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        raw_copper_data=raw_copper_data,
        pitch_deck_text=None,
        status=status,
    )


def _fake_card(rated: bool, bucket: str = "YES"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        user_override=None,
        user_rating="up" if rated else None,
        confidence_score=80,
        summary="promising deep-tech team",
        red_flags=[],
        scoring_breakdown={},
        research_data={},
    )


def _fake_action(lead_id, action_type="archive_no_reply", prior_status="assessed",
                  prior_tags=None, email_sent=False, copper_outbox_id=None, undone_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        action_type=action_type,
        actor_email="reviewer@raed.vc",
        prior_state={"status": prior_status, "copper_id": None, "copper_tags": prior_tags or []},
        email_sent=email_sent,
        copper_outbox_id=copper_outbox_id,
        undone_at=undone_at,
        created_at=datetime.now(timezone.utc),
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


def _override_db(results) -> _FakeSession:
    session = _FakeSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 1. archive_no_reply snapshots prior state
# ---------------------------------------------------------------------------

def test_archive_no_reply_snapshots_action_log(override_auth, monkeypatch):
    monkeypatch.setattr(
        copper_writer, "archive_in_copper",
        lambda *a, **k: "11111111-1111-1111-1111-111111111111",
    )

    lead = _fake_lead(status="assessed", copper_id="98765", raw_copper_data={"tags": ["existing-tag"]})
    card = _fake_card(rated=True, bucket="REJECT")
    session = _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert lead.status == "archived"

    from app.models.lead_action_log import LeadActionLog
    logged = [o for o in session.added if isinstance(o, LeadActionLog)]
    assert len(logged) == 1
    row = logged[0]
    assert row.action_type == undo_service.ACTION_ARCHIVE_NO_REPLY
    assert row.prior_state["status"] == "assessed"
    assert row.prior_state["copper_tags"] == ["existing-tag"]
    assert row.copper_outbox_id == "11111111-1111-1111-1111-111111111111"
    assert row.email_sent is False


def test_archive_no_reply_snapshots_even_without_copper_id(override_auth):
    """A lead never synced to Copper still gets a local undo snapshot -- undo
    should work for pure app state too."""
    lead = _fake_lead(status="pending", copper_id=None)
    session = _override_db([lead, None])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    from app.models.lead_action_log import LeadActionLog
    logged = [o for o in session.added if isinstance(o, LeadActionLog)]
    assert len(logged) == 1
    assert logged[0].prior_state["status"] == "pending"
    assert logged[0].prior_state["copper_tags"] == []


# ---------------------------------------------------------------------------
# 2. POST /leads/{lead_id}/undo -- happy path
# ---------------------------------------------------------------------------

def test_undo_restores_status_and_reverses_copper_write(override_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        copper_writer, "reverse_archive_in_copper",
        lambda copper_id, prior_tags, pending_outbox_id=None: calls.append(
            (copper_id, prior_tags, pending_outbox_id)
        ) or "22222222-2222-2222-2222-222222222222",
    )

    lead = _fake_lead(status="archived", copper_id="98765")
    action = _fake_action(
        lead.id, prior_status="assessed", prior_tags=["existing-tag"],
        copper_outbox_id="11111111-1111-1111-1111-111111111111",
    )
    override_row = SimpleNamespace(reverted_at=None, created_at=datetime.now(timezone.utc))
    _override_db([lead, action, override_row])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "undone"
    assert body["restored_status"] == "assessed"
    assert body["copper_enqueued"] is True
    assert body["email_sent"] is False

    assert lead.status == "assessed"
    assert action.undone_at is not None
    assert override_row.reverted_at is not None

    assert calls == [("98765", ["existing-tag"], "11111111-1111-1111-1111-111111111111")]


def test_undo_flags_but_does_not_recall_a_sent_email(override_auth, monkeypatch):
    monkeypatch.setattr(copper_writer, "reverse_archive_in_copper", lambda *a, **k: None)

    lead = _fake_lead(status="archived", copper_id="98765")
    action = _fake_action(
        lead.id, action_type=undo_service.ACTION_ARCHIVE_AFTER_SEND,
        prior_status="assessed", email_sent=True,
    )
    _override_db([lead, action, None])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["email_sent"] is True
    assert "cannot be unsent" in body["note"]
    # Undo never touches the assessment card, so sent_at (not modeled here at
    # all) is never reachable/re-armable from this code path.


def test_undo_is_idempotent(override_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        copper_writer, "reverse_archive_in_copper",
        lambda *a, **k: calls.append(a) or None,
    )

    lead = _fake_lead(status="assessed", copper_id="98765")  # already restored
    action = _fake_action(lead.id, undone_at=datetime.now(timezone.utc))
    _override_db([lead, action])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"status": "already_undone", "action_type": "archive_no_reply"}
    assert calls == []  # no second Copper write


def test_undo_refuses_when_lead_status_has_drifted(override_auth):
    lead = _fake_lead(status="approved")  # not "archived" anymore -- drifted
    action = _fake_action(lead.id)
    _override_db([lead, action])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "changed" in response.json()["detail"].lower()
    assert lead.status == "approved"  # untouched


def test_undo_refuses_converted_opportunity(override_auth):
    lead = _fake_lead(status="archived", copper_opportunity_id="opp-1")
    action = _fake_action(lead.id)
    _override_db([lead, action])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "opportunity" in response.json()["detail"].lower()


def test_undo_404s_when_nothing_to_undo(override_auth):
    lead = _fake_lead(status="archived")
    _override_db([lead, None])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 3. copper_writer: reverse_archive_in_copper / cancel_pending_outbox
# ---------------------------------------------------------------------------

def test_reverse_archive_in_copper_restores_status_and_clears_unqual_fields(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_reason_id", 244358)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_detail_id", 244359)

    cancelled = []
    monkeypatch.setattr(copper_writer, "cancel_pending_outbox", lambda oid: cancelled.append(oid))

    enqueued = []
    monkeypatch.setattr(
        copper_writer, "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(
            {"copper_id": copper_id, "endpoint": endpoint, "body": body}
        ) or "new-outbox-id",
    )

    result = copper_writer.reverse_archive_in_copper(
        "55555", ["raed:bucket:yes", "some-real-tag"], pending_outbox_id="old-outbox-id",
    )

    assert result == "new-outbox-id"
    assert cancelled == ["old-outbox-id"]
    assert len(enqueued) == 1
    body = enqueued[0]["body"]
    assert body["status_id"] == 737640
    assert body["tags"] == ["raed:bucket:yes", "some-real-tag"]  # restored exactly, not stripped
    assert body["custom_fields"] == [
        {"custom_field_definition_id": 244358, "value": []},
        {"custom_field_definition_id": 244359, "value": ""},
    ]


def test_reverse_archive_in_copper_skips_when_open_status_unset(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_open_status_id", 0)
    monkeypatch.setattr(copper_writer, "cancel_pending_outbox", lambda oid: None)
    monkeypatch.setattr(copper_writer, "_record_skipped_write", lambda *a, **k: None)

    enqueued = []
    monkeypatch.setattr(copper_writer, "_enqueue", lambda *a, **k: enqueued.append(a))

    result = copper_writer.reverse_archive_in_copper("55555", [])

    assert result is None
    assert enqueued == []


def test_cancel_pending_outbox_noop_when_no_id():
    assert copper_writer.cancel_pending_outbox(None) is False
    assert copper_writer.cancel_pending_outbox("") is False


def test_archive_in_copper_returns_enqueued_row_id(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 999)
    monkeypatch.setattr(copper_writer, "_enqueue", lambda *a, **k: "the-row-id")

    assert copper_writer.archive_in_copper("55555", ["some-tag"]) == "the-row-id"


# ---------------------------------------------------------------------------
# 4. feedback_patterns excludes reverted rows
# ---------------------------------------------------------------------------

class _RecordingSession:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []

    async def execute(self, query):
        self.queries.append(str(query))
        return SimpleNamespace(all=lambda: self._rows)


def test_retrieve_labeled_exemplars_excludes_reverted_rows():
    session = _RecordingSession([])
    asyncio.run(feedback_patterns.retrieve_labeled_exemplars(session, "deep tech logistics robotics"))

    assert any("reverted_at" in q and "IS NULL" in q for q in session.queries)
