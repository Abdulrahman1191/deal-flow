"""
Tests for GET /api/v1/users/roster (issue #214): unlike /users/team (the
"view as" dropdown, which excludes the caller by design), /roster is for
pages like Reassign Leads where the signed-in admin must be selectable as
both a source and a target, so it must include the caller and attach each
teammate's display name from the `User` row when one exists.

Same fake-session pattern as test_associates_performance.py: no live
Postgres needed -- a fake session's execute() just hands back canned User-
shaped rows for the router's select(User).where(email.in_(...)).
"""
from __future__ import annotations
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeSession:
    def __init__(self, users):
        self._users = users

    async def execute(self, query):
        return _FakeResult(self._users)


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(users):
    async def _fake_get_db():
        yield _FakeSession(users)

    app.dependency_overrides[get_db] = _fake_get_db


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def test_roster_includes_caller_and_colleagues(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", f"{OWNER_EMAIL},{COLLEAGUE_EMAIL},yomna@raed.vc")
    users = [
        SimpleNamespace(email=OWNER_EMAIL, full_name="Abdulrahman"),
        SimpleNamespace(email=COLLEAGUE_EMAIL, full_name="Waleed"),
    ]
    _auth_as(OWNER_EMAIL)
    _use_db(users)
    try:
        response = client.get("/api/v1/users/roster")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    by_email = {row["email"]: row["name"] for row in response.json()}
    assert OWNER_EMAIL in by_email
    assert by_email[OWNER_EMAIL] == "Abdulrahman"
    assert by_email[COLLEAGUE_EMAIL] == "Waleed"
    # No User row for yomna -- name falls back to None, entry still present.
    assert "yomna@raed.vc" in by_email
    assert by_email["yomna@raed.vc"] is None


def test_roster_excludes_non_client_facing(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", f"{OWNER_EMAIL},{COLLEAGUE_EMAIL},almuhammed@raed.vc")
    monkeypatch.setattr(settings, "non_client_facing_emails", "almuhammed@raed.vc")
    _auth_as(OWNER_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/users/roster")
    finally:
        _clear_auth()
        _clear_db()

    emails = [row["email"] for row in response.json()]
    assert "almuhammed@raed.vc" not in emails
    assert COLLEAGUE_EMAIL in emails


def test_non_admin_gets_403(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", f"{OWNER_EMAIL},{COLLEAGUE_EMAIL}")
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/users/roster")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403
