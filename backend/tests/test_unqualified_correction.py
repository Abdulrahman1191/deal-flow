"""
Tests for issue #157: correcting the Copper disposition when a lead moves out
of REJECT (via override_bucket) after already being written back as
Unqualified.

Unlike the fix-round-0 draft of this issue, the "was this lead already
written back to Copper as Unqualified" check is NOT a dedicated
Lead.copper_unqualified_at column. It's read off lead_action_log (the issue
#153 undo-snapshot table, already merged to main): an unresolved
(undone_at IS NULL) row of one of the Unqualified-writing action types
already means "we wrote Unqualified and haven't reversed it" -- reusing it
means archives that never touch Copper (lead dedup, the Copper-delete webhook
mirror) can't be mistaken for one, since neither of those ever logs to that
table, whereas the rejection-send archive, archive-no-reply, manual delete,
and bulk-archive all do (see test_undo_archive.py /
test_bulk_archive_writeback_task.py for those four write-side tests).

Uses the same fake-session / TestClient pattern as test_stale_draft_guard.py:
no live Postgres needed. `_get_card_and_lead` issues one `db.execute` (the
joined card+lead lookup); when the override is REJECT->YES/MAYBE and
lead.copper_id is set, override_bucket issues a second `db.execute` (the
lead_action_log lookup) before writing to Copper.
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


def _fake_lead(lead_id=None, copper_id="copper-123", status="archived"):
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
        status=status,
    )


def _fake_action(lead_id, action_type=undo_service.ACTION_ARCHIVE_AFTER_SEND, copper_outbox_id="outbox-1"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        action_type=action_type,
        copper_outbox_id=copper_outbox_id,
        undone_at=None,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
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


def test_reject_to_yes_on_previously_unqualified_lead_corrects_and_consumes_action(override_auth, monkeypatch):
    """The core issue #157 scenario: a lead was sent as a rejection (Copper
    written back as Unqualified), then the partner overrides it REJECT->YES.
    The Copper write-back must reopen the status and clear the unqualification
    reason/detail custom fields via correct_unqualified_override, not just
    swap the bucket tag -- and the lead_action_log row must be marked
    consumed so a later override can't re-fire it."""
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id)
    action = _fake_action(lead.id)

    calls = []

    def _fake_correct(copper_id, new_bucket, existing_tags, pending_outbox_id=None):
        calls.append({
            "copper_id": copper_id, "new_bucket": new_bucket,
            "existing_tags": existing_tags, "pending_outbox_id": pending_outbox_id,
        })

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("set_bucket_tag should not be called when a correction is needed")

    monkeypatch.setattr(assessments_router.copper_writer, "correct_unqualified_override", _fake_correct)
    monkeypatch.setattr(assessments_router.copper_writer, "set_bucket_tag", _fail_if_called)

    _override_db([(card, lead), action])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["copper_id"] == "copper-123"
    assert calls[0]["new_bucket"] == "YES"
    assert calls[0]["pending_outbox_id"] == "outbox-1"

    # Idempotency: the action row is marked consumed so a later override
    # doesn't re-fire the correction.
    assert action.undone_at is not None


def test_reject_to_maybe_on_previously_unqualified_lead_also_corrects(override_auth, monkeypatch):
    """MAYBE is an equally valid destination per the acceptance criteria, not
    just YES."""
    def _fake_regen_maybe(*_a, **_kw):
        return {"draft_type": None, "draft_subject": None, "draft_body": None}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regen_maybe)

    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id)
    action = _fake_action(lead.id, action_type=undo_service.ACTION_ARCHIVE_NO_REPLY)

    calls = []
    monkeypatch.setattr(
        assessments_router.copper_writer, "correct_unqualified_override",
        lambda copper_id, new_bucket, existing_tags, pending_outbox_id=None: calls.append(new_bucket),
    )
    monkeypatch.setattr(
        assessments_router.copper_writer, "set_bucket_tag",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("set_bucket_tag should not be called")),
    )

    _override_db([(card, lead), action])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "MAYBE"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == ["MAYBE"]
    assert action.undone_at is not None


def test_override_without_prior_unqualified_action_is_a_plain_tag_swap(override_auth, monkeypatch):
    """A lead overridden REJECT->YES with no unresolved lead_action_log row --
    either it was never archived, or it was archived by something that never
    writes Unqualified to Copper (dedup, the Copper-delete webhook mirror) --
    must not trigger the correction path. Guards against sending pointless
    writes."""
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id, status="pending")

    correct_calls = []
    tag_calls = []
    monkeypatch.setattr(
        assessments_router.copper_writer, "correct_unqualified_override",
        lambda *a, **kw: correct_calls.append(a),
    )
    monkeypatch.setattr(
        assessments_router.copper_writer, "set_bucket_tag",
        lambda copper_id, new_bucket, existing_tags: tag_calls.append(new_bucket),
    )

    _override_db([(card, lead), None])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert correct_calls == []
    assert tag_calls == ["YES"]


def test_already_consumed_action_is_not_returned_does_not_refire(override_auth, monkeypatch):
    """Once an action has already been consumed by a prior correction (or by
    /undo), the router's own query filters on undone_at IS NULL -- simulated
    here by queuing None, since the fake session doesn't apply real SQL
    WHERE clauses. A subsequent override must fall back to the plain tag
    swap rather than re-running the correction."""
    def _fake_regen_maybe(*_a, **_kw):
        return {"draft_type": None, "draft_subject": None, "draft_body": None}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regen_maybe)

    card = _fake_card(bucket="YES", draft_type="meeting_request", user_override="YES")
    lead = _fake_lead(lead_id=card.lead_id, status="assessed")

    correct_calls = []
    tag_calls = []
    monkeypatch.setattr(
        assessments_router.copper_writer, "correct_unqualified_override",
        lambda *a, **kw: correct_calls.append(a),
    )
    monkeypatch.setattr(
        assessments_router.copper_writer, "set_bucket_tag",
        lambda copper_id, new_bucket, existing_tags: tag_calls.append(new_bucket),
    )

    # Re-override YES -> MAYBE: prior_bucket is "YES", not "REJECT", so the
    # correction lookup query never even runs -- only one db.execute call.
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "MAYBE"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert correct_calls == []
    assert tag_calls == ["MAYBE"]


def test_override_without_copper_id_never_queries_action_log(override_auth, monkeypatch):
    """A lead with no copper_id has nothing to mirror to Copper at all --
    override_bucket must not issue the lead_action_log lookup (only the
    initial card+lead query), let alone call either Copper writer."""
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    card = _fake_card(bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(lead_id=card.lead_id, copper_id=None)

    monkeypatch.setattr(
        assessments_router.copper_writer, "correct_unqualified_override",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    monkeypatch.setattr(
        assessments_router.copper_writer, "set_bucket_tag",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/override", json={"bucket": "YES"})
    finally:
        _clear_db_override()

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# copper_writer.correct_unqualified_override
# ---------------------------------------------------------------------------

def test_correct_unqualified_override_swaps_bucket_tag_and_cancels_pending_write(monkeypatch):
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

    result = copper_writer.correct_unqualified_override(
        "55555", "YES", ["raed:archived", "raed:bucket:reject", "some-real-tag"],
        pending_outbox_id="old-outbox-id",
    )

    assert result == "new-outbox-id"
    assert cancelled == ["old-outbox-id"]
    assert len(enqueued) == 1
    body = enqueued[0]["body"]
    assert body["status_id"] == 737640
    assert body["tags"] == ["some-real-tag", "raed:bucket:yes", "raed:override"]
    assert body["custom_fields"] == [
        {"custom_field_definition_id": 244358, "value": []},
        {"custom_field_definition_id": 244359, "value": ""},
    ]


def test_correct_unqualified_override_noop_without_copper_id():
    assert copper_writer.correct_unqualified_override(None, "YES", []) is None


def test_correct_unqualified_override_degrades_when_open_status_unset(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_open_status_id", 0)
    monkeypatch.setattr(copper_writer, "cancel_pending_outbox", lambda oid: None)
    monkeypatch.setattr(copper_writer, "_record_skipped_write", lambda *a, **k: None)

    enqueued = []
    monkeypatch.setattr(copper_writer, "_enqueue", lambda *a, **k: enqueued.append(a))

    result = copper_writer.correct_unqualified_override("55555", "MAYBE", [])

    assert result is None
    assert enqueued == []
