"""
Tests for the Copper `delete` webhook branch of ingest_lead, and the new
GET /leads/orphans endpoint (issue #174).

Copper's `delete` event fires both for a genuine delete and for the losing
side of a merge -- these tests pin the distinction: a live twin (same
normalized company name or contact email) means archive-as-merge and name
the survivor; no twin means archive-as-delete AND log
`copper_record_vanished` so a partner-visible lead disappearing is
auditable via GET /leads/orphans, never silent. Also covers the signature
gate end-to-end: a bad/missing signature must 401 without touching the DB.

Mirrors the TestClient + dependency-override + `_FakeSession` pattern used
in test_leads_webhook_reassignment.py.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.routers import leads as leads_router
from app.services.auth import get_current_user

client = TestClient(app)


class _FakeResult:
    """Backs both `scalar_one_or_none()` (single-row lookups) and
    `scalars().all()` / `.all()` (the merge-twin scan and the orphans
    join) -- whichever the code under test calls, `_value` is returned
    as-is."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class _RaisingSession:
    """Used to prove a rejected webhook never touches the DB."""

    async def execute(self, _query):
        raise AssertionError("must not touch the DB when the signature is invalid")


def _lead(**overrides):
    base = dict(
        id=uuid.uuid4(),
        copper_id="copper-1",
        status="pending",
        owner_email="abdulrahman@raed.vc",
        company_name="Acme Co",
        raw_copper_data={"recipient_email": "founder@acme.com"},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _wire_db(session):
    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _post(copper_id="copper-1", signature="sig"):
    headers = {}
    if signature is not None:
        headers["X-Copper-Signature"] = signature
    return client.post(
        "/api/v1/leads/ingest",
        json={"event": "delete", "ids": [copper_id]},
        headers=headers,
    )


# --- merge vs. true delete -------------------------------------------------


def test_delete_with_live_company_name_twin_archives_as_merge(monkeypatch):
    lead = _lead()
    twin = _lead(id=uuid.uuid4(), copper_id="copper-2", company_name="ACME CO ",
                 raw_copper_data={"recipient_email": "someone-else@example.com"})

    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))
    session = _FakeSession([lead, [twin]])
    _wire_db(session)
    try:
        response = _post()
    finally:
        _clear_db()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "archived"
    assert body["reason"] == "merged_in_copper"
    assert body["surviving_lead_id"] == str(twin.id)
    assert lead.status == "archived"

    assert [e.event_type for e in session.added] == ["archived"]
    assert session.added[0].payload == {
        "reason": "merged_in_copper",
        "surviving_lead_id": str(twin.id),
    }


def test_delete_with_live_email_twin_archives_as_merge(monkeypatch):
    """Same-email match must fire even when the company name differs --
    Copper merges sometimes rename the surviving record."""
    lead = _lead(company_name="Acme Co", raw_copper_data={"recipient_email": "Founder@Acme.com"})
    twin = _lead(id=uuid.uuid4(), copper_id="copper-2", company_name="Totally Different Name",
                 raw_copper_data={"recipient_email": "founder@acme.com"})

    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))
    session = _FakeSession([lead, [twin]])
    _wire_db(session)
    try:
        response = _post()
    finally:
        _clear_db()

    assert response.status_code == 202
    body = response.json()
    assert body["reason"] == "merged_in_copper"
    assert body["surviving_lead_id"] == str(twin.id)


def test_delete_with_no_twin_archives_as_deleted_and_logs_vanished(monkeypatch):
    lead = _lead()

    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))
    session = _FakeSession([lead, []])
    _wire_db(session)
    try:
        response = _post()
    finally:
        _clear_db()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "archived"
    assert body["reason"] == "deleted_in_copper"
    assert lead.status == "archived"

    event_types = [e.event_type for e in session.added]
    assert event_types == ["archived", "copper_record_vanished"]
    assert session.added[0].payload == {"reason": "deleted_in_copper"}
    assert session.added[1].payload == {"copper_id": "copper-1", "company_name": "Acme Co"}


def test_delete_already_archived_lead_is_ignored(monkeypatch):
    lead = _lead(status="archived")

    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))
    session = _FakeSession([lead])
    _wire_db(session)
    try:
        response = _post()
    finally:
        _clear_db()

    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "event": "delete"}
    assert session.added == []


def test_delete_unknown_copper_id_is_ignored(monkeypatch):
    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))
    session = _FakeSession([None])
    _wire_db(session)
    try:
        response = _post(copper_id="does-not-exist")
    finally:
        _clear_db()

    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "event": "delete"}


# --- signature gate ---------------------------------------------------------


def test_bad_signature_returns_401_and_touches_nothing(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", "real-secret")
    _wire_db(_RaisingSession())
    try:
        response = _post(signature="not-the-right-signature")
    finally:
        _clear_db()

    assert response.status_code == 401


def test_missing_signature_header_returns_401_and_touches_nothing(monkeypatch):
    monkeypatch.setattr(settings, "copper_webhook_secret", "real-secret")
    _wire_db(_RaisingSession())
    try:
        response = _post(signature=None)
    finally:
        _clear_db()

    assert response.status_code == 401


def test_unconfigured_secret_fails_closed_returns_401(monkeypatch):
    """SECURITY_AUDIT.md F3: no secret configured must never be treated as
    "skip verification"."""
    monkeypatch.setattr(settings, "copper_webhook_secret", "")
    _wire_db(_RaisingSession())
    try:
        response = _post(signature="anything")
    finally:
        _clear_db()

    assert response.status_code == 401


# --- GET /leads/orphans ------------------------------------------------------


def test_orphans_forbidden_for_non_admin():
    _auth_as("waleed@raed.vc")
    _wire_db(_FakeSession([]))
    try:
        response = client.get("/api/v1/leads/orphans")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403


def test_orphans_lists_vanished_leads_for_admin():
    lead = _lead(company_name="Ghost Co")
    event = SimpleNamespace(
        payload={"copper_id": "copper-9", "company_name": "Ghost Co"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _auth_as(settings.owner_email)
    session = _FakeSession([[(event, lead)]])
    _wire_db(session)
    try:
        response = client.get("/api/v1/leads/orphans")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["orphans"][0]["company_name"] == "Ghost Co"
    assert body["orphans"][0]["copper_id"] == "copper-9"
    assert body["orphans"][0]["lead_id"] == str(lead.id)
