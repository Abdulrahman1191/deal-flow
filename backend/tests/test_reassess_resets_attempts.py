"""
Tests for resetting assessment_attempts at explicit re-assessment triggers
(PR #130 review follow-up).

The MAX_ASSESS_ATTEMPTS dead-letter guard (issue #129, app/tasks/assess_lead.py)
only resets assessment_attempts on a clean outcome inside _run() -- and a
dead-lettered lead ('failed') never reaches a clean outcome again on its own.
Without a reset, every future enqueue just keeps incrementing the already
poisoned counter, so assess_lead_task immediately dead-letters the lead again
without ever running. That silently broke both explicit re-assessment paths:
POST /api/v1/assessments/{lead_id}/reassess and the Copper webhook
material-change path in leads.py. Both now reset assessment_attempts = 0 in
the same transaction that flips status back to 'pending'.
"""
from __future__ import annotations
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.tasks import assess_lead
from app.tasks.assess_lead import assess_lead_task

client = TestClient(app)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


# ---------- task layer: a reset counter actually lets the task run again ----------


def test_reassess_after_dead_letter_reruns_instead_of_dead_lettering(monkeypatch):
    """Mirrors the fixed sequence end-to-end at the task layer: a lead that
    was dead-lettered well past MAX_ASSESS_ATTEMPTS has its counter reset to
    0 by an explicit re-assessment trigger (as /reassess and the webhook now
    do) before assess_lead_task runs again -- it must actually run _run()
    instead of immediately dead-lettering."""
    lead_id = str(uuid.uuid4())
    counter = {"value": 0}  # already reset by the caller, mirroring the fix

    def _increment(_lid):
        counter["value"] += 1
        return counter["value"]

    monkeypatch.setattr(assess_lead, "_increment_attempts", _increment)

    ran = []

    async def _fake_run(lid):
        ran.append(lid)
        return {"lead_id": lid, "status": "assessed"}

    monkeypatch.setattr(assess_lead, "_run", _fake_run)
    monkeypatch.setattr(
        assess_lead,
        "_mark_failed",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not dead-letter a lead whose attempts counter was reset")
        ),
    )

    result = assess_lead_task(lead_id)

    assert result == {"lead_id": lead_id, "status": "assessed"}
    assert ran == [lead_id]


# ---------- router: /reassess resets the counter ----------


def _dead_lettered_lead(owner_email="reviewer@raed.vc"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email=owner_email,
        status="failed",
        assessment_attempts=assess_lead.MAX_ASSESS_ATTEMPTS + 2,
    )


async def _fake_current_user():
    return SimpleNamespace(email="reviewer@raed.vc", is_active=True)


def test_reassess_endpoint_resets_attempts_counter(monkeypatch):
    lead = _dead_lettered_lead()

    class _FakeSession:
        async def execute(self, _query):
            return _FakeResult(lead)

        async def commit(self):
            pass

    async def _fake_get_db():
        yield _FakeSession()

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lid: queued.append(lid))

    app.dependency_overrides[get_current_user] = _fake_current_user
    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = client.post(f"/api/v1/assessments/{lead.id}/reassess")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    assert response.json() == {"status": "queued"}
    assert lead.status == "pending"
    assert lead.assessment_attempts == 0
    assert queued == [str(lead.id)]


# ---------- router: Copper webhook material-change path resets the counter too ----------


def test_webhook_material_change_resets_attempts_counter(monkeypatch):
    from app.routers import leads as leads_router
    from app.services import copper_service

    lead = SimpleNamespace(
        id=uuid.uuid4(),
        copper_id="copper-123",
        status="failed",
        assessment_attempts=assess_lead.MAX_ASSESS_ATTEMPTS + 2,
        company_name="Old Name",
        website="https://old.example.com",
        description="old description",
        founder_names=None,
        stage=None,
        region=None,
        company_linkedin_url=None,
        raw_copper_data={},
    )

    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))
    monkeypatch.setattr(
        copper_service,
        "fetch_lead_by_id",
        lambda copper_id: {
            "id": "copper-123",
            "name": "New Name",
            "details": "a materially different description",
        },
    )

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lid: queued.append(lid))

    class _FakeSession:
        async def execute(self, _query):
            return _FakeResult(lead)

        def add(self, _obj):
            pass

        async def commit(self):
            pass

    async def _fake_get_db():
        yield _FakeSession()

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = client.post(
            "/api/v1/leads/ingest",
            json={"event": "updated", "ids": ["copper-123"]},
            headers={"X-Copper-Signature": "sig"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    assert response.json()["status"] == "synced_and_reassessing"
    assert lead.status == "pending"
    assert lead.assessment_attempts == 0
    assert queued == [str(lead.id)]
