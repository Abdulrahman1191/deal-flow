"""
Admin "view as" QA mode (issue #50), backend-only pass.

An admin (ADMIN_EMAILS allow-list, defaults to `owner_email`) can pass
`?view_as=<email>` to read a teammate's board exactly as that teammate sees
it. A non-admin passing `view_as` is silently ignored. The mode is strictly
read-only: any mutating endpoint 403s while `view_as` is honored.

Exercised the same way as test_multiuser_access.py: fastapi.testclient against
dependency overrides for get_current_user/get_db, with a _RecordingSession
that records bound query params so tests can assert *which* email a query was
actually scoped to (not just that the canned fixture happens to match).
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.services.auth import (
    block_if_impersonating,
    effective_owner_email,
    get_current_user,
    is_impersonating,
)

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"
OTHER_COLLEAGUE_EMAIL = "yomna@raed.vc"


# ---------- shared fakes (mirrors test_multiuser_access.py) ----------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else []

    def first(self):
        return self._value


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)
        self.queries: list[dict] = []
        self.added: list = []

    async def execute(self, query):
        try:
            params = dict(query.compile().params)
        except Exception:
            params = {}
        self.queries.append(params)
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


def _bound_values(session: _RecordingSession, call_index: int) -> list:
    return list(session.queries[call_index].values())


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(results) -> _RecordingSession:
    session = _RecordingSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def _fake_lead_row(owner_email: str, company_name: str = "Acme Deep Tech", lead_id=None):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        copper_id=None,
        owner_email=owner_email,
        company_name=company_name,
        website=None,
        description=None,
        stage=None,
        region=None,
        founder_names=None,
        linkedin_urls=None,
        company_linkedin_url=None,
        pitch_deck_filename=None,
        pitch_deck_ingested_at=None,
        pitch_deck_drive_id=None,
        status="pending",
        created_at=now,
        updated_at=now,
        assessment=None,
    )


def _fake_card(rated: bool, bucket: str = "YES", lead_id=None):
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
        draft_subject="Let's talk",
        draft_body="Hi there",
        draft_type="meeting_request" if bucket == "YES" else "rejection",
        research_sources=[],
        research_data={},
        user_override=None,
        user_override_at=None,
        user_rating="up" if rated else None,
        user_rating_at=now if rated else None,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )


def _fake_lead_for_assessment(owner_email: str, lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email=owner_email,
        copper_id=None,
        copper_opportunity_id=None,
        company_name="Acme Deep Tech",
        founder_names=["Founder One"],
        raw_copper_data={"recipient_email": "founder@acme.test"},
        pitch_deck_text=None,
        status="pending",
    )


# ---------- effective_owner_email / is_impersonating unit behavior ----------


def test_effective_owner_email_honors_view_as_for_admin():
    request = SimpleNamespace(query_params={"view_as": COLLEAGUE_EMAIL.upper()})
    admin = SimpleNamespace(email=OWNER_EMAIL)
    assert effective_owner_email(request, admin) == COLLEAGUE_EMAIL


def test_effective_owner_email_ignores_view_as_for_non_admin():
    request = SimpleNamespace(query_params={"view_as": OWNER_EMAIL})
    colleague = SimpleNamespace(email=COLLEAGUE_EMAIL)
    assert effective_owner_email(request, colleague) == COLLEAGUE_EMAIL


def test_effective_owner_email_defaults_to_self_when_absent():
    request = SimpleNamespace(query_params={})
    admin = SimpleNamespace(email=OWNER_EMAIL)
    assert effective_owner_email(request, admin) == OWNER_EMAIL


def test_is_impersonating_true_only_for_admin_with_view_as():
    admin = SimpleNamespace(email=OWNER_EMAIL)
    colleague = SimpleNamespace(email=COLLEAGUE_EMAIL)
    assert is_impersonating(SimpleNamespace(query_params={"view_as": COLLEAGUE_EMAIL}), admin) is True
    assert is_impersonating(SimpleNamespace(query_params={}), admin) is False
    assert is_impersonating(SimpleNamespace(query_params={"view_as": OWNER_EMAIL}), colleague) is False


def test_block_if_impersonating_raises_403_only_when_honored():
    from fastapi import HTTPException

    admin = SimpleNamespace(email=OWNER_EMAIL)
    with pytest.raises(HTTPException) as exc:
        block_if_impersonating(SimpleNamespace(query_params={"view_as": COLLEAGUE_EMAIL}), admin)
    assert exc.value.status_code == 403

    # No-op when not impersonating.
    block_if_impersonating(SimpleNamespace(query_params={}), admin)
    colleague = SimpleNamespace(email=COLLEAGUE_EMAIL)
    block_if_impersonating(SimpleNamespace(query_params={"view_as": OWNER_EMAIL}), colleague)


def test_view_as_honored_logs_audit_line(caplog):
    import logging

    request = SimpleNamespace(query_params={"view_as": COLLEAGUE_EMAIL})
    admin = SimpleNamespace(email=OWNER_EMAIL)
    with caplog.at_level(logging.INFO, logger="app.services.auth"):
        effective_owner_email(request, admin)
    assert any(
        "view_as honored" in r.message and OWNER_EMAIL in r.message and COLLEAGUE_EMAIL in r.message
        for r in caplog.records
    )


# ---------- leads list / detail / archive: admin can read via view_as ----------


def test_admin_reads_colleagues_leads_via_view_as():
    their_lead = _fake_lead_row(COLLEAGUE_EMAIL, "Their Co")
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [their_lead]])
    try:
        response = client.get(f"/api/v1/leads?view_as={COLLEAGUE_EMAIL}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["owner_email"] == COLLEAGUE_EMAIL
    for call in range(len(session.queries)):
        values = _bound_values(session, call)
        assert COLLEAGUE_EMAIL in values
        assert OWNER_EMAIL not in values


def test_non_admin_view_as_is_ignored_and_sees_only_own_leads():
    my_lead = _fake_lead_row(COLLEAGUE_EMAIL, "My Co")
    _auth_as(COLLEAGUE_EMAIL)
    session = _use_db([1, [my_lead]])
    try:
        response = client.get(f"/api/v1/leads?view_as={OWNER_EMAIL}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["owner_email"] == COLLEAGUE_EMAIL
    for call in range(len(session.queries)):
        values = _bound_values(session, call)
        assert COLLEAGUE_EMAIL in values
        assert OWNER_EMAIL not in values


def test_admin_reads_colleagues_lead_detail_via_view_as():
    lead_id = uuid.uuid4()
    their_lead = _fake_lead_row(COLLEAGUE_EMAIL, lead_id=lead_id)
    _auth_as(OWNER_EMAIL)
    session = _use_db([their_lead])
    try:
        response = client.get(f"/api/v1/leads/{lead_id}?view_as={COLLEAGUE_EMAIL}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    values = _bound_values(session, 0)
    assert COLLEAGUE_EMAIL in values
    assert OWNER_EMAIL not in values


def test_admin_reads_colleagues_archive_via_view_as():
    _auth_as(OWNER_EMAIL)
    session = _use_db([[]])
    try:
        response = client.get(f"/api/v1/leads/archive/list?view_as={COLLEAGUE_EMAIL}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    values = _bound_values(session, 0)
    assert COLLEAGUE_EMAIL in values
    assert OWNER_EMAIL not in values


def test_admin_reads_colleagues_send_queue_via_view_as():
    _auth_as(OWNER_EMAIL)
    session = _use_db([[]])
    try:
        response = client.get(f"/api/v1/assessments/send-queue?view_as={COLLEAGUE_EMAIL}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    values = _bound_values(session, 0)
    assert COLLEAGUE_EMAIL in values
    assert OWNER_EMAIL not in values


def test_admin_reads_colleagues_assessment_via_view_as():
    card = _fake_card(rated=True)
    lead = _fake_lead_for_assessment(COLLEAGUE_EMAIL, lead_id=card.lead_id)
    _auth_as(OWNER_EMAIL)
    session = _use_db([(card, lead)])
    try:
        response = client.get(f"/api/v1/assessments/{card.lead_id}?view_as={COLLEAGUE_EMAIL}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    values = _bound_values(session, 0)
    assert COLLEAGUE_EMAIL in values
    assert OWNER_EMAIL not in values


# ---------- mutations 403 while impersonating ----------


MUTATION_CASES = [
    ("POST", "/api/v1/assessments/{lead_id}/rate", {"rating": "up"}),
    ("POST", "/api/v1/assessments/{lead_id}/override", {"bucket": "YES"}),
    ("POST", "/api/v1/assessments/{lead_id}/approve", None),
    ("POST", "/api/v1/assessments/{lead_id}/send", None),
    ("POST", "/api/v1/assessments/{lead_id}/mark-sent", None),
    ("POST", "/api/v1/assessments/{lead_id}/reassess", None),
    ("PATCH", "/api/v1/assessments/{lead_id}/draft", {"draft_subject": "New subject"}),
    ("POST", "/api/v1/assessments/{lead_id}/regenerate-draft", None),
    ("POST", "/api/v1/leads/{lead_id}/archive-no-reply", None),
    ("GET", "/api/v1/leads/{lead_id}/pitch-deck", None),
]


@pytest.mark.parametrize("method,path_tmpl,payload", MUTATION_CASES)
def test_mutation_returns_403_while_impersonating(method, path_tmpl, payload):
    lead_id = uuid.uuid4()
    path = path_tmpl.format(lead_id=lead_id) + f"?view_as={COLLEAGUE_EMAIL}"
    _auth_as(OWNER_EMAIL)
    # No DB dependency override needed: block_if_impersonating fires before
    # any query executes, so a missing get_db override would only matter if
    # the request got past the guard.
    _use_db([])
    try:
        response = client.request(method, path, json=payload)
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403
    assert "Read-only while viewing another user's board" in response.json()["detail"]


def test_normal_non_impersonating_mutation_is_unaffected():
    """Regression guard: without `view_as`, a rating mutation for the caller's
    own lead still works exactly as before (no accidental 403)."""
    card = _fake_card(rated=False)
    lead = _fake_lead_for_assessment(OWNER_EMAIL, lead_id=card.lead_id)
    _auth_as(OWNER_EMAIL)
    session = _use_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert card.user_rating == "up"


def test_normal_non_impersonating_read_is_unaffected():
    my_lead = _fake_lead_row(OWNER_EMAIL, "My Co")
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [my_lead]])
    try:
        response = client.get("/api/v1/leads")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["owner_email"] == OWNER_EMAIL
