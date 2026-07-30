"""
Tests for issue #109: `GET /leads` accepts `sort=newest|oldest` and flips the
`ORDER BY Lead.created_at` direction accordingly, defaulting to `newest`
(current behavior) when the param is omitted.

Mirrors the _RecordingSession pattern in test_multiuser_access.py /
test_awaiting_deck_board_filter.py, but additionally records the *raw* query
object (not just its bound params) so the test can inspect the compiled
ORDER BY clause directly -- this is what proves the router actually flipped
`.asc()`/`.desc()` rather than just trusting canned fixture data (which would
look identical either way since order isn't re-derived from the fixture
list).
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

    async def execute(self, query):
        self.raw_queries.append(query)
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


def _fake_lead_row(owner_email: str, company_name: str = "Acme Deep Tech"):
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
        status="pending",
        created_at=now,
        updated_at=now,
        assessment=None,
    )


def _order_by_sql(session: _RecordingSession) -> str:
    """The page query (second execute call) is the one with ORDER BY --
    the first is the count() subquery."""
    page_query = session.raw_queries[1]
    return str(page_query.compile(compile_kwargs={"literal_binds": False}))


def test_default_sort_is_newest_first_descending():
    lead = _fake_lead_row(OWNER_EMAIL)
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [lead]])
    try:
        response = client.get("/api/v1/leads")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    sql = _order_by_sql(session)
    assert "ORDER BY leads.created_at DESC" in sql


def test_sort_newest_is_explicit_descending():
    lead = _fake_lead_row(OWNER_EMAIL)
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [lead]])
    try:
        response = client.get("/api/v1/leads?sort=newest")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    sql = _order_by_sql(session)
    assert "ORDER BY leads.created_at DESC" in sql


def test_sort_oldest_returns_ascending_order_by():
    lead = _fake_lead_row(OWNER_EMAIL)
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [lead]])
    try:
        response = client.get("/api/v1/leads?sort=oldest")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    sql = _order_by_sql(session)
    assert "ORDER BY leads.created_at ASC" in sql


def test_invalid_sort_value_is_rejected():
    _auth_as(OWNER_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/leads?sort=bogus")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 422
