"""
Tests for issue #225: a thumbs-down on a YES lead auto-rejects it in one
action, instead of requiring a separate bucket override.

Same fake-session / TestClient pattern as test_unqualified_correction.py:
no live Postgres. `_get_card_and_lead` issues one `db.execute` (the joined
card+lead lookup); the auto-reject path adds no further `execute()` calls
when `lead.owner_email` is None (so `_load_owner_draft_fields` short-circuits
without a query) -- only `db.add()`/`db.commit()` calls, which the fake
session no-ops.
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
from app.services import copper_writer, feedback_patterns, undo as undo_service
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


def _fake_card(bucket: str, user_override=None, draft_type="meeting_request", rejection_reasons=None):
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
        draft_subject="Let's talk" if draft_type else None,
        draft_body="We'd love to meet." if draft_type else None,
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
        raw_copper_data={"recipient_email": "founder@acme.test", "tags": ["raed:bucket:yes"]},
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


def _fake_regenerate_rejection(*_args, **_kwargs):
    return {
        "draft_type": "rejection",
        "draft_subject": "Thanks for applying",
        "draft_body": "Thanks, but this isn't a fit right now.",
    }


# ---------------------------------------------------------------------------
# 1. thumbs-down on YES auto-rejects
# ---------------------------------------------------------------------------


def test_rate_down_on_yes_sets_override_reject_logs_event_and_regenerates_draft(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    events = []

    async def _fake_log_event(db, lead_id, event_type, payload=None):
        events.append((event_type, payload))

    monkeypatch.setattr(assessments_router, "log_event", _fake_log_event)

    card = _fake_card(bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.user_override == "REJECT"
    assert card.bucket == "REJECT"
    assert card.draft_type == "rejection"
    assert card.draft_subject == "Thanks for applying"
    assert card.draft_body == "Thanks, but this isn't a fit right now."
    assert card.draft_bucket == "REJECT"

    assert ("bucket_overridden", {"from": "YES", "to": "REJECT", "source": "rate_down_auto"}) in events


def test_rate_down_on_yes_via_prior_override_also_auto_rejects(override_auth, monkeypatch):
    """Effective bucket is YES because of an earlier manual override (AI said
    MAYBE, partner overrode to YES) -- a thumbs-down on THAT must still
    auto-reject, since it's the effective bucket that matters."""
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    card = _fake_card(bucket="MAYBE", user_override="YES", draft_type="meeting_request")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.user_override == "REJECT"
    assert card.bucket == "REJECT"


def test_rate_down_on_yes_mirrors_bucket_tag_to_copper(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    calls = []
    monkeypatch.setattr(
        copper_writer, "set_bucket_tag",
        lambda copper_id, new_bucket, existing_tags: calls.append((copper_id, new_bucket, existing_tags)),
    )

    card = _fake_card(bucket="YES")
    lead = _fake_lead(lead_id=card.lead_id, copper_id="copper-123")
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [("copper-123", "REJECT", ["raed:bucket:yes"])]


# ---------------------------------------------------------------------------
# 2. no regression: MAYBE/REJECT thumbs-down, and thumbs-up on YES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bucket", ["MAYBE", "REJECT"])
def test_rate_down_on_maybe_or_reject_changes_no_bucket(override_auth, monkeypatch, bucket):
    def _fail_if_called(*_a, **_k):
        raise AssertionError("must not regenerate a draft for a non-auto-reject rating")

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fail_if_called)
    monkeypatch.setattr(
        copper_writer, "set_bucket_tag",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not mirror to Copper")),
    )

    draft_type = "rejection" if bucket == "REJECT" else None
    card = _fake_card(bucket=bucket, draft_type=draft_type)
    lead = _fake_lead(lead_id=card.lead_id, copper_id="copper-123")
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.bucket == bucket
    assert card.user_override is None
    assert card.draft_type == draft_type


def test_rate_up_on_yes_changes_no_bucket(override_auth, monkeypatch):
    def _fail_if_called(*_a, **_k):
        raise AssertionError("must not regenerate a draft on a thumbs-up")

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fail_if_called)

    card = _fake_card(bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.bucket == "YES"
    assert card.user_override is None
    assert card.draft_type == "meeting_request"


# ---------------------------------------------------------------------------
# 3. training row
# ---------------------------------------------------------------------------


def test_auto_reject_training_row_records_ai_yes_human_reject(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    card = _fake_card(bucket="YES")
    lead = _fake_lead(lead_id=card.lead_id)
    session = _override_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{card.lead_id}/rate",
            json={"rating": "down", "reason_tags": ["Not MENA"], "reason": "wrong geography"},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200

    from app.models.override import AssessmentOverride
    rows = [o for o in session.added if isinstance(o, AssessmentOverride)]
    assert len(rows) == 1
    row = rows[0]
    assert row.ai_bucket == "YES"
    assert row.human_bucket == "REJECT"
    assert row.trigger == "rate_down_auto_reject"
    assert row.human_reason_tags == ["Not MENA"]
    assert row.human_reason == "wrong geography"


def test_plain_rate_down_training_row_trigger_unchanged(override_auth, monkeypatch):
    """Regression: MAYBE/REJECT thumbs-down must still record the plain
    'rate_down' trigger, not the new auto-reject one."""
    card = _fake_card(bucket="MAYBE", draft_type=None)
    lead = _fake_lead(lead_id=card.lead_id)
    session = _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    from app.models.override import AssessmentOverride
    rows = [o for o in session.added if isinstance(o, AssessmentOverride)]
    assert rows[0].trigger == "rate_down"
    assert rows[0].ai_bucket == "MAYBE"
    assert rows[0].human_bucket == "MAYBE"


def test_rate_down_auto_reject_trigger_is_usable_and_formats_as_correction():
    """feedback_patterns must treat the new trigger as a strong correction
    (like override/re-override), not drop it or treat it as a plain
    caution like rate_down."""
    assert "rate_down_auto_reject" in feedback_patterns._USABLE_TRIGGERS

    rendered = feedback_patterns.format_for_prompt([
        {
            "company": "Acme", "summary": "deep tech", "ai_bucket": "YES",
            "human_bucket": "REJECT", "trigger": "rate_down_auto_reject",
            "reason": None, "reason_tags": None,
        }
    ])
    assert "CORRECTED YES → REJECT" in rendered


# ---------------------------------------------------------------------------
# 4. reason tags land in rejection_reasons
# ---------------------------------------------------------------------------


def test_reason_tags_land_in_rejection_reasons(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    card = _fake_card(bucket="YES")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{card.lead_id}/rate",
            json={"rating": "down", "reason_tags": ["Not MENA", "Too early stage"]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.rejection_reasons == ["Not MENA", "Too early stage"]


def test_no_reason_tags_leaves_rejection_reasons_untouched(override_auth, monkeypatch):
    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate_rejection)

    card = _fake_card(bucket="YES", rejection_reasons=None)
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.rejection_reasons is None


# ---------------------------------------------------------------------------
# 5. draft-regeneration failure nulls the draft
# ---------------------------------------------------------------------------


def test_draft_regen_failure_nulls_draft_instead_of_leaving_meeting_request(override_auth, monkeypatch):
    def _always_fail(*_a, **_k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _always_fail)
    monkeypatch.setattr(assessments_router, "_DRAFT_REGEN_MAX_ATTEMPTS", 1)

    card = _fake_card(bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(lead_id=card.lead_id)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "down"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert card.user_override == "REJECT"
    assert card.bucket == "REJECT"
    assert card.draft_type is None
    assert card.draft_subject is None
    assert card.draft_body is None
    assert card.draft_bucket is None


# ---------------------------------------------------------------------------
# 6. undo restores YES, clears the override, and restores the previous draft
# ---------------------------------------------------------------------------


def _fake_bucket_override_action(lead_id, new_bucket="REJECT", undone_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        action_type=undo_service.ACTION_BUCKET_OVERRIDE,
        actor_email="reviewer@raed.vc",
        prior_state={
            "bucket": "YES",
            "user_override": None,
            "user_override_at": None,
            "draft_type": "meeting_request",
            "draft_subject": "Let's talk",
            "draft_body": "We'd love to meet.",
            "draft_bucket": "YES",
            "rejection_reasons": None,
            "new_bucket": new_bucket,
            "copper_id": "copper-123",
            "copper_tags": ["raed:bucket:yes"],
        },
        email_sent=False,
        copper_outbox_id=None,
        undone_at=undone_at,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


class _UndoFakeSession(_FakeSession):
    pass


def _lead_for_undo(lead_id=None, copper_id="copper-123", copper_opportunity_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        status="assessed",
    )


def test_undo_restores_yes_clears_override_and_restores_draft(override_auth, monkeypatch):
    tag_calls = []
    monkeypatch.setattr(
        copper_writer, "set_bucket_tag",
        lambda copper_id, new_bucket, existing_tags: tag_calls.append((copper_id, new_bucket, existing_tags)),
    )

    lead = _lead_for_undo()
    action = _fake_bucket_override_action(lead.id)
    card = _fake_card(bucket="REJECT", user_override="REJECT", draft_type="rejection")

    session = _override_db([lead, action, card, None])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "undone"
    assert body["restored_bucket"] == "YES"

    assert card.bucket == "YES"
    assert card.user_override is None
    assert card.draft_type == "meeting_request"
    assert card.draft_subject == "Let's talk"
    assert card.draft_body == "We'd love to meet."
    assert card.draft_bucket == "YES"
    assert action.undone_at is not None
    assert tag_calls == [("copper-123", "YES", ["raed:bucket:yes"])]


def test_undo_refuses_when_bucket_has_drifted(override_auth):
    lead = _lead_for_undo()
    action = _fake_bucket_override_action(lead.id)
    # Card's effective bucket is no longer REJECT (e.g. a later manual
    # override moved it to MAYBE) -- undo must refuse rather than clobber it.
    card = _fake_card(bucket="MAYBE", user_override="MAYBE")

    _override_db([lead, action, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "changed" in response.json()["detail"].lower()


def test_undo_is_idempotent_for_bucket_override(override_auth):
    lead = _lead_for_undo()
    action = _fake_bucket_override_action(lead.id, undone_at=datetime.now(timezone.utc))

    _override_db([lead, action])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"status": "already_undone", "action_type": "bucket_override"}
