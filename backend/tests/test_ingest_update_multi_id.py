"""
Copper `update` notifications naming several leads on POST /api/v1/leads/ingest.

Copper aggregates notifications, so one body's `ids` can list several leads
(e.g. a partner bulk-editing or bulk-reassigning). The update branch used to
act on ids[0] only and silently drop the rest, and the echo guard keyed its
registry check on ids[0] too, so one echoed id could drop a whole batch.

Bodies are Copper's notification shape: {ids, type, event, subscription_id,
timestamp, updated_attributes} (see the recorded `new` body in
test_ingest_copper_notification.py; 560697 is the production `update`
subscription), with updated_attributes as Copper's {field: [old, new]}.
"""
from __future__ import annotations
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.main import app
from app.routers import leads as leads_router
from app.services import copper_service
from app.tasks.assess_lead import assess_lead_task

client = TestClient(app)


def _update_notification(ids, updated_attributes=None):
    return {
        "ids": ids,
        "type": "lead",
        "event": "update",
        "timestamp": "2026-09-30T03:46:12.004Z",
        "subscription_id": 560697,
        "updated_attributes": updated_attributes or {"status": ["New", "Contacted"]},
    }


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Answers each Lead lookup by copper_id from `leads_by_id` (None = unknown
    locally), in the order the route asks."""

    def __init__(self, lookup_order, leads_by_id, commit_error=None):
        self._order = list(lookup_order)
        self._leads = leads_by_id
        self.commit_error = commit_error
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        return _FakeResult(self._leads.get(self._order.pop(0)))

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


def _lead(copper_id, **overrides):
    base = dict(
        id=uuid.uuid4(), copper_id=copper_id, status="assessed", owner_email="waleed@raed.vc",
        assessment_attempts=0, company_name=f"Co {copper_id}", website=None,
        description="same description", founder_names=[f"Founder {copper_id}"],
        stage=None, region=None, company_linkedin_url=None, raw_copper_data={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fresh(copper_id):
    """What fetch_lead_by_id returns: the lead as Copper holds it now,
    mapping to the same watched fields as _lead() (no material change)."""
    return {"id": int(copper_id), "name": f"Founder {copper_id}", "company_name": f"Co {copper_id}",
            "details": "same description"}


def _wire(monkeypatch, session, echo_ids=()):
    fetched, queued = [], []
    monkeypatch.setattr(leads_router, "verify_webhook_signature", lambda *a, **k: True)
    monkeypatch.setattr(leads_router, "is_recent_echo",
                        lambda cid, attrs: (cid in echo_ids, "registry-hit" if cid in echo_ids else ""))

    async def _no_reassign(*_a, **_k):
        return None

    async def _owner(*_a, **_k):
        return "uday@raed.vc"
    monkeypatch.setattr(leads_router, "_resolve_reassignment_owner", _no_reassign)
    monkeypatch.setattr(leads_router, "_owner_for_assignee", _owner)

    def _fetch(cid):
        fetched.append(cid)
        return _fresh(cid)
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


def _events(session):
    return [(e.lead_id, e.event_type) for e in session.added if hasattr(e, "event_type")]


def test_every_id_in_an_update_notification_is_synced(monkeypatch):
    a, b, c = _lead("94808926"), _lead("94808986"), _lead("94808992")
    session = _FakeSession(["94808926", "94808986", "94808992"],
                           {"94808926": a, "94808986": b, "94808992": c})
    fetched, _ = _wire(monkeypatch, session)

    r = _post(_update_notification([94808926, 94808986, 94808992]))

    assert r.status_code == 202
    assert fetched == ["94808926", "94808986", "94808992"]
    body = r.json()
    assert body["status"] == "processed" and body["count"] == 3
    assert [x["status"] for x in body["results"]] == ["synced", "synced", "synced"]
    assert {lid for lid, t in _events(session) if t == "copper_updated"} == {a.id, b.id, c.id}


def test_unknown_id_in_a_batch_is_imported_alongside_known_ones(monkeypatch):
    known = _lead("94808926")
    session = _FakeSession(["94808926", "94809008"], {"94808926": known, "94809008": None})
    _, queued = _wire(monkeypatch, session)

    r = _post(_update_notification([94808926, 94809008]))

    assert r.status_code == 202
    assert [x["status"] for x in r.json()["results"]] == ["synced", "queued_from_update"]
    new_rows = [o for o in session.added if getattr(o, "copper_id", None) == "94809008"]
    assert len(new_rows) == 1 and new_rows[0].owner_email == "uday@raed.vc"
    assert len(queued) == 1


def test_one_echoed_id_does_not_drop_the_rest_of_the_batch(monkeypatch):
    a, b = _lead("94808926"), _lead("94808986")
    session = _FakeSession(["94808986"], {"94808926": a, "94808986": b})
    fetched, _ = _wire(monkeypatch, session, echo_ids={"94808926"})

    r = _post(_update_notification([94808926, 94808986]))

    assert r.status_code == 202
    results = r.json()["results"]
    assert results[0] == {"status": "echo_dropped", "reason": "registry-hit", "copper_id": "94808926"}
    assert results[1]["status"] == "synced"
    assert fetched == ["94808986"]  # the echoed id is never fetched or touched


def test_single_id_update_keeps_its_old_response_shape(monkeypatch):
    lead = _lead("94808926")
    session = _FakeSession(["94808926"], {"94808926": lead})
    _wire(monkeypatch, session)

    r = _post(_update_notification([94808926]))

    assert r.status_code == 202
    assert r.json() == {"lead_id": str(lead.id), "status": "synced"}


def test_single_id_echo_is_still_dropped_before_any_work(monkeypatch):
    session = _FakeSession([], {})
    fetched, _ = _wire(monkeypatch, session, echo_ids={"94808926"})

    r = _post(_update_notification([94808926]))

    assert r.status_code == 202
    assert r.json() == {"status": "echo_dropped", "reason": "registry-hit"}
    assert fetched == []


def test_update_import_racing_the_sync_is_a_duplicate_not_a_500(monkeypatch):
    race = IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))
    session = _FakeSession(["94809008"], {"94809008": None}, commit_error=race)
    _, queued = _wire(monkeypatch, session)

    r = _post(_update_notification([94809008]))

    assert r.status_code == 202
    assert r.json() == {"status": "duplicate", "copper_id": "94809008"}
    assert session.rollbacks == 1 and queued == []


def test_update_without_ids_is_ignored(monkeypatch):
    session = _FakeSession([], {})
    fetched, _ = _wire(monkeypatch, session)

    r = _post(_update_notification([]))

    assert r.status_code == 202
    assert r.json()["reason"] == "no_id"
    assert fetched == []
