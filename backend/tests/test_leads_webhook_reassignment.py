"""
Tests for reassignment handling in the Copper webhook `update` branch
(app/routers/leads.py::ingest_lead, issue #171).

Before this fix, `ingest_lead`'s update branch re-fetched the lead from
Copper and copied every mapped field over the local row -- but
map_copper_lead() never returns owner_email, and _owner_for_assignee was only
called on the brand-new-lead path. A reassignment arriving by webhook updated
every field except the one that moved, and (had it been wired up naively)
would also have re-triggered assessment, since a reassignment has nothing to
do with the assessment-relevant `watched` fields.

Mirrors the `_FakeSession`/queued-result pattern used in
test_reconcile_ownership_task.py and the webhook test in
test_reassess_resets_attempts.py.
"""
from __future__ import annotations
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import copper_service
from app.tasks.assess_lead import assess_lead_task
from app.routers import leads as leads_router

client = TestClient(app)

YOMNA_COPPER_ID = 852916
ABDULRAHMAN_COPPER_ID = 1181364


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns queued results in order -- one per db.execute() call. The
    update branch's Lead lookup is always first; a User lookup for
    reassignment (when the payload carries an assignee_id) is second."""

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


def _lead(**overrides):
    base = dict(
        id=uuid.uuid4(),
        copper_id="copper-123",
        status="pending",
        owner_email="abdulrahman@raed.vc",
        assessment_attempts=0,
        company_name="Acme Co",
        website=None,
        description="same description",
        founder_names=["Acme Co"],
        stage=None,
        region=None,
        company_linkedin_url=None,
        raw_copper_data={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _wire(monkeypatch, session, fresh_payload):
    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))
    monkeypatch.setattr(copper_service, "fetch_lead_by_id", lambda copper_id: fresh_payload)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db


def _post(fresh_payload_ids=("copper-123",)):
    return client.post(
        "/api/v1/leads/ingest",
        json={"event": "updated", "ids": list(fresh_payload_ids)},
        headers={"X-Copper-Signature": "sig"},
    )


def test_reassignment_updates_owner_and_logs_event_without_reassessing(monkeypatch):
    """Acceptance criterion 1: a different, known assignee_id updates
    owner_email and writes a `reassigned` event; no re-queue, status
    unchanged."""
    lead = _lead()
    yomna = SimpleNamespace(email="yomna@raed.vc")
    session = _FakeSession([lead, yomna])

    fresh = {
        "id": "copper-123",
        "name": "Acme Co",
        "details": "same description",
        "assignee_id": YOMNA_COPPER_ID,
    }
    _wire(monkeypatch, session, fresh)

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lid: queued.append(lid))

    try:
        response = _post()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    assert lead.owner_email == "yomna@raed.vc"
    assert lead.status == "pending"  # unchanged
    assert queued == []  # never re-queued for assessment

    event_types = [e.event_type for e in session.added]
    assert "reassigned" in event_types
    reassigned_event = next(e for e in session.added if e.event_type == "reassigned")
    assert reassigned_event.payload == {
        "from_owner": "abdulrahman@raed.vc",
        "to_owner": "yomna@raed.vc",
        "source": "webhook",
    }


def test_unknown_assignee_leaves_owner_untouched(monkeypatch):
    """Acceptance criterion 2: an assignee_id that doesn't map to a known
    app user must leave owner_email as-is, not blank/default it."""
    lead = _lead()
    session = _FakeSession([lead, None])  # User query resolves to nobody

    fresh = {
        "id": "copper-123",
        "name": "Acme Co",
        "details": "same description",
        "assignee_id": 999999999,
    }
    _wire(monkeypatch, session, fresh)

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lid: queued.append(lid))

    try:
        response = _post()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    assert lead.owner_email == "abdulrahman@raed.vc"
    assert queued == []
    assert all(e.event_type != "reassigned" for e in session.added)


def test_watched_field_change_still_reassesses_with_no_assignee_id(monkeypatch):
    """Regression check: a payload with no assignee_id at all (the common
    case -- most updates aren't reassignments) still reassesses on a
    material change exactly as before this fix, and never touches
    owner_email."""
    lead = _lead()
    session = _FakeSession([lead])  # no User lookup -- no assignee_id present

    fresh = {
        "id": "copper-123",
        "name": "Acme Co",
        "details": "a materially different description",
    }
    _wire(monkeypatch, session, fresh)

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lid: queued.append(lid))

    try:
        response = _post()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    assert response.json()["status"] == "synced_and_reassessing"
    assert lead.status == "pending"
    assert lead.owner_email == "abdulrahman@raed.vc"
    assert queued == [str(lead.id)]
    assert all(e.event_type != "reassigned" for e in session.added)


def test_reassignment_and_material_change_together_do_both_independently(monkeypatch):
    """A reassignment landing in the same webhook as a real content edit
    still reassesses (material change) AND reassigns ownership -- the two
    are independent, since assignee_id isn't in the `watched` tuple."""
    lead = _lead()
    yomna = SimpleNamespace(email="yomna@raed.vc")
    session = _FakeSession([lead, yomna])

    fresh = {
        "id": "copper-123",
        "name": "Acme Co",
        "details": "a materially different description",
        "assignee_id": YOMNA_COPPER_ID,
    }
    _wire(monkeypatch, session, fresh)

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lid: queued.append(lid))

    try:
        response = _post()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    assert lead.owner_email == "yomna@raed.vc"
    assert queued == [str(lead.id)]
    assert any(e.event_type == "reassigned" for e in session.added)
