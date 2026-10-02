"""
Who may assert X-Auth-Email (app/services/auth.py caller_gate, wired in app/main.py).

Before this, deal-flow trusted X-Auth-Email from anything on the raed_platform
network (17 containers on 2026-10-02, incl. analyst prototypes), so any of them
could act as any partner, /approve and /send included. Reem now reads deal-flow
directly with a service token restricted to four routes; the platform proxy's
own secret header closes the forged-header path for everyone else.
"""
from __future__ import annotations
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import settings
from app.main import app
from app.services import auth

TOKEN = "t" * 64
PROXY = "p" * 64
LEAD = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setattr(settings, "dealflow_service_token", TOKEN)
    monkeypatch.setattr(settings, "raed_proxy_secret", "")


def _req(method, path, headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": method, "path": path, "headers": raw, "query_string": b""})


def _svc(email="analyst@raed.vc", token=TOKEN):
    return {"X-Service-Token": token, "X-Auth-Email": email}


# --- service token: allowlist -----------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/leads"),
    ("GET", f"/api/v1/leads/{LEAD}"),
    ("GET", f"/api/v1/assessments/{LEAD}"),
    ("POST", f"/api/v1/assessments/{LEAD}/rate"),
])
def test_service_token_allows_exactly_the_four_reem_routes(method, path):
    assert auth.caller_gate(_req(method, path, _svc())) is None


@pytest.mark.parametrize("method,path", [
    ("POST", f"/api/v1/assessments/{LEAD}/approve"),
    ("POST", f"/api/v1/assessments/{LEAD}/send"),
    ("POST", f"/api/v1/assessments/{LEAD}/mark-sent"),
    ("POST", f"/api/v1/assessments/{LEAD}/override"),
    ("POST", f"/api/v1/assessments/{LEAD}/reassess"),
    ("POST", "/api/v1/leads/bulk-archive"),
    ("POST", f"/api/v1/leads/{LEAD}/archive-no-reply"),
    ("GET", "/api/v1/leads/export"),
    ("GET", "/api/v1/leads/orphans"),
    ("GET", "/api/v1/leads/failed-summary"),
    ("GET", "/api/v1/leads/archive/list"),
    ("GET", f"/api/v1/leads/{LEAD}/events"),
    ("GET", "/api/v1/assessments/send-queue"),
    ("GET", "/api/v1/leads/"),               # trailing slash is not the allowlisted path
    ("DELETE", f"/api/v1/leads/{LEAD}"),
    ("POST", "/api/v1/leads"),
])
def test_service_token_is_refused_everywhere_else(method, path):
    assert auth.caller_gate(_req(method, path, _svc()))[0] == 403


def test_wrong_or_empty_service_token_is_401():
    assert auth.caller_gate(_req("GET", "/api/v1/leads", _svc(token="nope")))[0] == 401
    assert auth.caller_gate(_req("GET", "/api/v1/leads", _svc(token="")))[0] == 401


def test_service_token_is_refused_when_none_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "dealflow_service_token", "")
    assert auth.caller_gate(_req("GET", "/api/v1/leads", _svc(token="")))[0] == 401


@pytest.mark.parametrize("email", ["", "someone@gmail.com", "@raed.vc", "x@raed.vc.evil.com"])
def test_service_calls_must_act_as_a_raed_user(email):
    assert auth.caller_gate(_req("GET", "/api/v1/leads", _svc(email=email)))[0] == 403


def test_reem_qa_seat_is_allowed():
    assert auth.caller_gate(_req("GET", "/api/v1/leads", _svc(email="reem-qa@raed.vc"))) is None


# --- proxy secret (part b) --------------------------------------------------------

def test_without_a_proxy_secret_configured_behaviour_is_unchanged():
    assert auth.caller_gate(_req("POST", f"/api/v1/assessments/{LEAD}/send", {"X-Auth-Email": "p@raed.vc"})) is None


def test_with_a_proxy_secret_a_forged_direct_header_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "raed_proxy_secret", PROXY)
    forged = _req("POST", f"/api/v1/assessments/{LEAD}/send", {"X-Auth-Email": "partner@raed.vc"})
    assert auth.caller_gate(forged)[0] == 401
    wrong = _req("GET", "/api/v1/leads", {"X-Auth-Email": "partner@raed.vc", "X-Raed-Proxy": "guess"})
    assert auth.caller_gate(wrong)[0] == 401


def test_with_a_proxy_secret_proxied_users_and_reem_still_get_in(monkeypatch):
    monkeypatch.setattr(settings, "raed_proxy_secret", PROXY)
    proxied = _req("POST", f"/api/v1/assessments/{LEAD}/send", {"X-Auth-Email": "p@raed.vc", "X-Raed-Proxy": PROXY})
    assert auth.caller_gate(proxied) is None
    assert auth.caller_gate(_req("GET", "/api/v1/leads", _svc())) is None


def test_requests_without_an_identity_are_untouched(monkeypatch):
    """Copper's webhook and health checks assert no identity; they're unaffected."""
    monkeypatch.setattr(settings, "raed_proxy_secret", PROXY)
    assert auth.caller_gate(_req("POST", "/api/v1/leads/ingest", {"X-Copper-Webhook-Token": "x"})) is None
    assert auth.caller_gate(_req("GET", "/api/v1/health")) is None


# --- wired into the app -----------------------------------------------------------

client = TestClient(app, raise_server_exceptions=False)


def test_app_rejects_service_token_on_send_before_any_route_runs():
    r = client.post(f"/api/v1/assessments/{LEAD}/send", headers=_svc())
    assert r.status_code == 403
    assert "not allowed" in r.json()["detail"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"  # security headers still applied


def test_app_rejects_forged_header_when_proxy_secret_set(monkeypatch):
    monkeypatch.setattr(settings, "raed_proxy_secret", PROXY)
    r = client.get("/api/v1/leads", headers={"X-Auth-Email": "partner@raed.vc"})
    assert r.status_code == 401
    assert "platform proxy" in r.json()["detail"]


def test_app_lets_an_allowlisted_service_call_through_to_the_route():
    from app.services.auth import get_current_user
    from app.database import get_db

    seen = {}

    async def _user():
        seen["route_reached"] = True
        raise RuntimeError("stop here: the gate let us through")

    async def _db():
        yield None

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_db] = _db
    try:
        r = client.get(f"/api/v1/leads/{LEAD}", headers=_svc())
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
    assert seen.get("route_reached") is True
    assert r.status_code not in (401, 403)
