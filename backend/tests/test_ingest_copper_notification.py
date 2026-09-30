"""
Copper `new` notifications on POST /api/v1/leads/ingest.

Copper's real notification body carries only ids (no lead fields). Parsing it
as a lead produced copper_id="" and company "Unknown": the first one inserted
a junk row, every later one violated leads_copper_id_key and 500'd (Copper
then redelivered each ~8 times). These tests replay the body Copper actually
sent (recorded on that junk row, 2026-09-29) and pin that each id is fetched
from Copper and imported once, that no row is ever inserted without a Copper
id, and that a duplicate -- including a race with the polling sync -- is a
2xx, never a 500.
"""
from __future__ import annotations
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.main import app
from app.routers import leads as leads_router
from app.services import copper_service
from app.tasks.assess_lead import assess_lead_task

client = TestClient(app)

# Verbatim from the junk row's raw_copper_data (minus our recipient_email key).
RECORDED_NEW_NOTIFICATION = {
    "ids": [94808710],
    "type": "lead",
    "event": "new",
    "timestamp": "2026-09-29T22:40:04.738Z",
    "subscription_id": 560696,
    "updated_attributes": {},
}


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """db.execute() answers the copper_id existence lookups in order;
    `commit_error` makes the next commit raise (the sync-race case)."""

    def __init__(self, lookups, commit_error=None):
        self._lookups = list(lookups)
        self.commit_error = commit_error
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        return _FakeResult(self._lookups.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.commit_error is not None:
            err, self.commit_error = self.commit_error, None
            raise err
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


def _copper_lead(copper_id):
    return {"id": int(copper_id), "name": f"Founder {copper_id}", "company_name": f"Co {copper_id}",
            "assignee_id": 111, "status_id": 1, "tags": []}


def _wire(monkeypatch, session, fetch=None):
    fetched, queued = [], []
    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo", lambda *a, **k: (False, None))

    async def _owner(_db, fresh):
        return "uday@raed.vc"
    monkeypatch.setattr(leads_router, "_owner_for_assignee", _owner)

    def _fetch(copper_id):
        fetched.append(copper_id)
        return (fetch or _copper_lead)(copper_id)
    monkeypatch.setattr(copper_service, "fetch_lead_by_id", _fetch)
    monkeypatch.setattr(assess_lead_task, "delay", lambda lid: queued.append(lid))

    async def _fake_get_db():
        yield session
    app.dependency_overrides[get_db] = _fake_get_db
    return fetched, queued


def _post(body):
    try:
        return client.post("/api/v1/leads/ingest", json=body, headers={"X-Copper-Webhook-Token": "t"})
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_recorded_copper_new_notification_imports_the_named_lead(monkeypatch):
    session = _FakeSession([None])
    fetched, queued = _wire(monkeypatch, session)

    r = _post(RECORDED_NEW_NOTIFICATION)

    assert r.status_code == 202
    assert fetched == ["94808710"]
    assert [l.copper_id for l in session.added] == ["94808710"]
    assert session.added[0].company_name != "Unknown"
    assert session.added[0].owner_email == "uday@raed.vc"
    assert len(queued) == 1
    assert r.json()["status"] == "queued"


def test_repeat_notification_for_a_known_lead_is_a_duplicate_not_a_500(monkeypatch):
    session = _FakeSession([object()])  # already on a row
    fetched, queued = _wire(monkeypatch, session)

    r = _post(RECORDED_NEW_NOTIFICATION)

    assert r.status_code == 202
    assert r.json() == {"status": "duplicate", "copper_id": "94808710"}
    assert fetched == [] and session.added == [] and queued == []


def test_race_with_polling_sync_is_a_duplicate_not_a_500(monkeypatch):
    """The sync inserted the same copper_id between our lookup and commit."""
    race = IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))
    session = _FakeSession([None], commit_error=race)
    _, queued = _wire(monkeypatch, session)

    r = _post(RECORDED_NEW_NOTIFICATION)

    assert r.status_code == 202
    assert r.json()["status"] == "duplicate"
    assert session.rollbacks == 1
    assert queued == []


def test_every_id_in_an_aggregated_notification_is_imported(monkeypatch):
    session = _FakeSession([None, object(), None])
    fetched, queued = _wire(monkeypatch, session)

    r = _post({**RECORDED_NEW_NOTIFICATION, "ids": [101, 102, 103]})

    assert r.status_code == 202
    assert fetched == ["101", "103"]
    assert [l.copper_id for l in session.added] == ["101", "103"]
    assert [x["status"] for x in r.json()["results"]] == ["queued", "duplicate", "queued"]


def test_notification_without_ids_inserts_nothing(monkeypatch):
    session = _FakeSession([])
    fetched, _ = _wire(monkeypatch, session)

    r = _post({**RECORDED_NEW_NOTIFICATION, "ids": []})

    assert r.status_code == 202
    assert r.json()["reason"] == "no_id"
    assert fetched == [] and session.added == []


def test_lead_deleted_before_fetch_inserts_nothing(monkeypatch):
    session = _FakeSession([None])
    _wire(monkeypatch, session, fetch=lambda _cid: None)

    r = _post(RECORDED_NEW_NOTIFICATION)

    assert r.status_code == 202
    assert r.json()["status"] == "not_found_in_copper"
    assert session.added == []


def test_copper_fetch_failure_asks_copper_to_redeliver(monkeypatch):
    def _boom(_cid):
        raise RuntimeError("copper down")
    session = _FakeSession([None])
    _wire(monkeypatch, session, fetch=_boom)

    r = _post(RECORDED_NEW_NOTIFICATION)

    assert r.status_code == 503
    assert session.added == []


def test_lead_payload_without_an_id_is_never_inserted(monkeypatch):
    """The direct-parse path must not write copper_id="" either."""
    session = _FakeSession([])
    _wire(monkeypatch, session)

    r = _post({"event": "new", "payload": {"name": "No Id Co", "email": []}})

    assert r.status_code == 202
    assert r.json()["reason"] == "no_id"
    assert session.added == []
