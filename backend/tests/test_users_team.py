"""
GET /api/v1/users/team — admin-only teammate list for the "view as" dropdown
(issue #52). Exercised the same way as test_view_as.py: fastapi.testclient
against a get_current_user dependency override; no DB dependency needed since
the endpoint only reads settings.team_email_list().
"""
from __future__ import annotations
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def test_admin_gets_team_list_excluding_self(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", f"{OWNER_EMAIL},{COLLEAGUE_EMAIL},yomna@raed.vc")
    _auth_as(OWNER_EMAIL)
    try:
        response = client.get("/api/v1/users/team")
    finally:
        _clear_auth()

    assert response.status_code == 200
    emails = response.json()
    assert OWNER_EMAIL not in emails
    assert COLLEAGUE_EMAIL in emails
    assert "yomna@raed.vc" in emails


def test_non_admin_gets_403(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", f"{OWNER_EMAIL},{COLLEAGUE_EMAIL}")
    _auth_as(COLLEAGUE_EMAIL)
    try:
        response = client.get("/api/v1/users/team")
    finally:
        _clear_auth()

    assert response.status_code == 403
