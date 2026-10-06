"""
Tests for issue #217's GET /api/v1/leads filters: `source` (repeatable),
`applied_from`, and `applied_to` (ISO dates, inclusive at both ends). They
must compose with the existing `bucket`/`status`/`search` params and apply
before pagination, so `total` reflects them.

Mirrors the _RecordingSession pattern in test_awaiting_deck_board_filter.py /
test_lead_sort_order.py -- no live DB, just inspecting the compiled query's
bound params (and WHERE clause text where bound values alone can't prove the
comparison operator used, e.g. inclusivity).
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = "owner@raed.vc"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else []


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)
        self.raw_queries: list = []
        self.queries: list[dict] = []

    async def execute(self, query):
        self.raw_queries.append(query)
        try:
            params = dict(query.compile().params)
        except Exception:
            params = {}
        self.queries.append(params)
        return _FakeResult(self._results.pop(0))


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


def _fake_lead_row(owner_email: str, company_name: str = "Acme Deep Tech", status: str = "pending"):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
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
        status=status,
        created_at=now,
        updated_at=now,
        assessment=None,
    )


def _bound_values(session: _RecordingSession, call_index: int) -> list:
    flat = []
    for value in session.queries[call_index].values():
        if isinstance(value, (list, tuple, set)):
            flat.extend(value)
        else:
            flat.append(value)
    return flat


def _where_sql(session: _RecordingSession, call_index: int) -> str:
    return str(session.raw_queries[call_index].compile(compile_kwargs={"literal_binds": True}))


def test_source_filter_accepts_multiple_values():
    lead = _fake_lead_row(OWNER_EMAIL)
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [lead]])
    try:
        response = client.get("/api/v1/leads?source=email_inbox&source=website_form")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    values = _bound_values(session, 0)
    assert "email_inbox" in values
    assert "website_form" in values


def test_source_filter_absent_by_default():
    lead = _fake_lead_row(OWNER_EMAIL)
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [lead]])
    try:
        response = client.get("/api/v1/leads")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    sql = _where_sql(session, 0)
    # "leads.source" always appears in the selected column list -- only the
    # absence of a filter predicate on it is being asserted here.
    assert "leads.source IN" not in sql
    assert "leads.source =" not in sql


def test_applied_from_and_to_are_inclusive_at_both_ends():
    lead = _fake_lead_row(OWNER_EMAIL)
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [lead]])
    try:
        response = client.get("/api/v1/leads?applied_from=2026-01-01&applied_to=2026-01-31")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    sql = _where_sql(session, 0)
    assert "leads.applied_at >= '2026-01-01 00:00:00" in sql
    assert "leads.applied_at <= '2026-01-31 23:59:59" in sql


def test_applied_filters_compose_with_bucket_status_and_search():
    lead = _fake_lead_row(OWNER_EMAIL, status="pending")
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [lead]])
    try:
        response = client.get(
            "/api/v1/leads"
            "?status=pending&search=Acme&source=website_form"
            "&applied_from=2026-01-01&applied_to=2026-01-31"
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    sql = _where_sql(session, 0)
    assert "leads.status" in sql
    assert "lower(leads.company_name) LIKE lower(" in sql
    assert "leads.source IN" in sql or "leads.source = " in sql
    assert "leads.applied_at >=" in sql
    assert "leads.applied_at <=" in sql


def test_total_reflects_the_composed_filters():
    """The count() subquery (the first execute call) must carry the same
    filters as the page query (the second) -- both are built from the same
    `query` object before `.offset()/.limit()` are applied."""
    lead = _fake_lead_row(OWNER_EMAIL)
    _auth_as(OWNER_EMAIL)
    session = _use_db([3, [lead]])
    try:
        response = client.get("/api/v1/leads?source=website_form&applied_from=2026-01-01")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3

    count_sql = _where_sql(session, 0)
    assert "website_form" in count_sql
    assert "2026-01-01" in count_sql
