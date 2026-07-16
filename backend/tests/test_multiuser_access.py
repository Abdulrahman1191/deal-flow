"""
Locks in the multi-user access-control matrix: per-user lead scoping, the
mandatory-rating gate, admin-gated feedback + portfolio, and the acting
user's email captured on /rate.

Exercised with fastapi.testclient.TestClient against app.database.get_db and
app.services.auth.get_current_user dependency overrides — the same pattern
used in test_assessment_rating_gate.py / test_csv_export.py, so no live
Postgres is needed and this stays green in CI (which has no DB service).

_RecordingSession additionally records the *bound parameter values* of every
query it executes (via Select.compile().params). That's what lets tests like
test_lead_list_is_scoped_to_acting_user assert the router actually filtered
by the acting user's email — not just that the canned fixture happened to
match — so a regression that drops a `.where(owner_email == ...)` clause
would show up as another user's email going missing from the bound params,
even though the canned return value is unchanged.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.override import AssessmentOverride
from app.schemas.lead import LeadOut
from app.services.auth import get_current_user, is_owner

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"
OTHER_COLLEAGUES = ["yomna@raed.vc", "uday@raed.vc"]


# ---------- shared fakes ----------


class _FakeResult:
    """Stands in for the SQLAlchemy Result returned by AsyncSession.execute.
    Covers every access pattern the routers under test use."""

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
    """Returns queued canned results per execute() call, in order (mirrors
    test_assessment_rating_gate.py), while recording each query's bound
    parameter values so tests can assert *which* user a query was scoped to.

    Bound values (rather than rendered SQL) sidestep CompileError on
    literal-rendering the Postgres UUID columns used across these models —
    parameter binding doesn't need a type's literal_processor, only its
    bind_processor, so this works regardless of column type.
    """

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
        # Mimic server-side defaults (gen_random_uuid()/now()) that a real
        # commit+refresh would populate — needed for rows like Feedback that
        # get serialized straight back out without an explicit refresh here.
        if getattr(obj, "id", "unset") is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", "unset") is None:
            obj.created_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
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


def _fake_feedback_row(user_email: str, feedback_id=None):
    return SimpleNamespace(
        id=feedback_id or uuid.uuid4(),
        user_email=user_email,
        page_url=None,
        category=None,
        message="The kanban is great",
        resolved_at=None,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


# ---------- auto-create + is_owner ----------


def test_new_raed_email_auto_creates_user():
    class _AutoCreateSession:
        def __init__(self):
            self.added = None

        async def execute(self, _query):
            return _FakeResult(None)

        def add(self, obj):
            self.added = obj

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    request = SimpleNamespace(headers={"X-Auth-Email": "newhire@raed.vc"}, query_params={})
    db = _AutoCreateSession()

    user = asyncio.run(get_current_user(request, db))

    assert user.email == "newhire@raed.vc"
    assert user.is_active is True
    assert db.added is user


def test_is_owner_true_for_configured_owner():
    assert is_owner(SimpleNamespace(email=OWNER_EMAIL)) is True


@pytest.mark.parametrize("email", [COLLEAGUE_EMAIL, *OTHER_COLLEAGUES])
def test_is_owner_false_for_colleague(email):
    assert is_owner(SimpleNamespace(email=email)) is False


# ---------- lead list scoping ----------


@pytest.mark.parametrize(
    "acting_email,other_email",
    [(COLLEAGUE_EMAIL, OWNER_EMAIL), (OWNER_EMAIL, COLLEAGUE_EMAIL)],
)
def test_lead_list_is_scoped_to_acting_user(acting_email, other_email):
    my_lead = _fake_lead_row(acting_email, "My Co")
    _auth_as(acting_email)
    session = _use_db([1, [my_lead]])
    try:
        response = client.get("/api/v1/leads")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["owner_email"] == acting_email

    # Both the count query and the page query must be bound to the acting
    # user's email, and never the other user's — this is what would catch a
    # dropped owner_email filter even though the fixture data looks right.
    for call in range(len(session.queries)):
        values = _bound_values(session, call)
        assert acting_email in values
        assert other_email not in values


def test_lead_out_schema_serializes_owner_email():
    assert "owner_email" in LeadOut.model_fields


# ---------- id-addressed lead access across users ----------


@pytest.mark.parametrize("acting_email", [COLLEAGUE_EMAIL, OWNER_EMAIL])
def test_lead_by_id_cross_user_returns_404(acting_email):
    """A user requesting a lead_id that doesn't belong to them gets 404 —
    including the owner requesting a colleague's lead (strict visibility:
    the owner does NOT get a backdoor to colleagues' leads)."""
    lead_id = uuid.uuid4()
    _auth_as(acting_email)
    session = _use_db([None])
    try:
        response = client.get(f"/api/v1/leads/{lead_id}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 404
    values = _bound_values(session, 0)
    assert acting_email in values
    assert str(lead_id) in values


# ---------- id-addressed assessment access across users (F2 regression) ----------


@pytest.mark.parametrize("acting_email", [COLLEAGUE_EMAIL, OWNER_EMAIL])
def test_assessment_cross_user_returns_404(acting_email):
    """A user requesting an assessment for a lead_id they don't own gets 404 —
    regression test for SECURITY_AUDIT.md F2, where /assessments/* previously
    had no owner_email scoping at all and would happily return (or mutate)
    another user's lead assessment."""
    lead_id = uuid.uuid4()
    _auth_as(acting_email)
    session = _use_db([None])
    try:
        response = client.get(f"/api/v1/assessments/{lead_id}")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 404
    values = _bound_values(session, 0)
    assert acting_email in values
    assert str(lead_id) in values


# ---------- rating gate ----------


@pytest.mark.parametrize("acting_email", [OWNER_EMAIL, COLLEAGUE_EMAIL, *OTHER_COLLEAGUES])
def test_rating_gate_applies_to_every_user(acting_email):
    card = _fake_card(rated=False)
    lead = _fake_lead_for_assessment(acting_email, lead_id=card.lead_id)
    _auth_as(acting_email)

    session = _use_db([(card, lead)])
    try:
        blocked = client.post(f"/api/v1/assessments/{card.lead_id}/approve")
    finally:
        _clear_db()
    assert blocked.status_code == 428

    session = _use_db([(card, lead)])
    try:
        rated = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": "up"})
    finally:
        _clear_db()
    assert rated.status_code == 200
    assert card.user_rating == "up"

    session = _use_db([(card, lead)])
    try:
        approved = client.post(f"/api/v1/assessments/{card.lead_id}/approve")
    finally:
        _clear_db()
        _clear_auth()
    assert approved.status_code == 200


# ---------- /rate records the acting user's email ----------


@pytest.mark.parametrize("acting_email,rating", [(COLLEAGUE_EMAIL, "up"), (OWNER_EMAIL, "down")])
def test_rate_records_acting_users_email(acting_email, rating):
    card = _fake_card(rated=False)
    lead = _fake_lead_for_assessment(acting_email, lead_id=card.lead_id)
    _auth_as(acting_email)
    session = _use_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/rate", json={"rating": rating})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    captured = [obj for obj in session.added if isinstance(obj, AssessmentOverride)]
    assert len(captured) == 1
    assert captured[0].acted_by_email == acting_email


# ---------- feedback: admin gate ----------


def test_colleague_can_submit_feedback():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    try:
        response = client.post("/api/v1/feedback", json={"message": "The kanban is great"})
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 201
    assert response.json()["user_email"] == COLLEAGUE_EMAIL


def test_feedback_inbox_blocked_for_colleague():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/feedback")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403


def test_feedback_inbox_allowed_for_owner():
    fb_row = _fake_feedback_row(COLLEAGUE_EMAIL)
    _auth_as(OWNER_EMAIL)
    _use_db([[fb_row]])
    try:
        response = client.get("/api/v1/feedback")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert response.json()[0]["user_email"] == COLLEAGUE_EMAIL


def test_feedback_resolve_blocked_for_colleague():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    try:
        response = client.post(f"/api/v1/feedback/{uuid.uuid4()}/resolve")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403


def test_feedback_resolve_allowed_for_owner():
    fb_row = _fake_feedback_row(COLLEAGUE_EMAIL)
    _auth_as(OWNER_EMAIL)
    _use_db([fb_row])
    try:
        response = client.post(f"/api/v1/feedback/{fb_row.id}/resolve")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert fb_row.resolved_at is not None


# ---------- portfolio: admin gate ----------


def test_portfolio_companies_blocked_for_colleague():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/portfolio/companies")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403


def test_portfolio_companies_allowed_for_owner():
    _auth_as(OWNER_EMAIL)
    _use_db([[]])
    try:
        response = client.get("/api/v1/portfolio/companies")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
