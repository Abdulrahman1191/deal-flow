"""
Tests for GET /associates/performance — the GP dashboard backend (issue
#125): per-associate lead-management throughput counts.

Same fake-session pattern as test_overrides_calibration.py: no live Postgres
in this suite. A recording session returns canned rows shaped exactly like
what the grouped SQL aggregate would hand back, so these tests exercise the
router's wiring/zero-fill logic rather than the SQL engine itself.
"""
from __future__ import annotations
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.routers.associates import ASSOCIATE_EMAILS
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
NON_ADMIN_EMAIL = "waleed@raed.vc"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)
        self.queries: list[str] = []
        self.params: list[dict] = []

    async def execute(self, query, params=None):
        self.queries.append(str(query))
        self.params.append(params or {})
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


def test_non_admin_gets_403():
    _auth_as(NON_ADMIN_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/associates/performance")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403


def test_returns_the_four_associates_not_almuhammed():
    rows = [
        ("abdulrahman@raed.vc", 10, 2, 1, 3, 4, 2, 1, 1),
        ("waleed@raed.vc", 5, 1, 0, 2, 2, 1, 0, 1),
        # uday and yomna intentionally omitted -- the router must zero-fill
        # them rather than dropping them from the response.
    ]

    _auth_as(OWNER_EMAIL)
    session = _use_db([rows])
    try:
        response = client.get("/api/v1/associates/performance")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    emails = [a["email"] for a in body["associates"]]

    assert emails == ASSOCIATE_EMAILS
    assert "almuhammed@raed.vc" not in emails

    # almuhammed must never even be bound as a query param
    for params in session.params:
        assert "almuhammed@raed.vc" not in params.values()


def test_correct_counts_for_present_associate():
    rows = [
        ("abdulrahman@raed.vc", 10, 2, 1, 3, 4, 2, 1, 1),
    ]

    _auth_as(OWNER_EMAIL)
    _use_db([rows])
    try:
        response = client.get("/api/v1/associates/performance")
    finally:
        _clear_auth()
        _clear_db()

    body = response.json()
    abdulrahman = next(a for a in body["associates"] if a["email"] == "abdulrahman@raed.vc")

    assert abdulrahman["leads_total"] == 10
    assert abdulrahman["backlog"] == 2
    assert abdulrahman["awaiting_deck"] == 1
    assert abdulrahman["active"] == 3
    assert abdulrahman["outreach_sent"] == 4
    assert abdulrahman["approved"] == 2
    assert abdulrahman["converted"] == 1
    assert abdulrahman["archived"] == 1


def test_zero_fills_associates_with_no_leads():
    _auth_as(OWNER_EMAIL)
    _use_db([[]])
    try:
        response = client.get("/api/v1/associates/performance")
    finally:
        _clear_auth()
        _clear_db()

    body = response.json()
    assert len(body["associates"]) == 4
    for a in body["associates"]:
        assert a["leads_total"] == 0
        assert a["backlog"] == 0
        assert a["awaiting_deck"] == 0
        assert a["active"] == 0
        assert a["outreach_sent"] == 0
        assert a["approved"] == 0
        assert a["converted"] == 0
        assert a["archived"] == 0
