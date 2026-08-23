"""
Tests for POST /leads/bulk-archive (issue #141): multi-select archive that
still fires the full Copper write-back (Unqualified + tags + best-effort AI
reason) per lead, exactly like the single archive-no-reply path -- but
WITHOUT that path's mandatory-rating gate, and with per-lead isolation so one
bad id / DB hiccup never aborts the rest of the batch.

The router only does the fast, synchronous part (status + event + commit)
and dispatches `bulk_archive_writeback_task` (one Celery task per lead) for
the slow part -- AI reason generation + the Copper write-back itself -- so a
large batch doesn't run N synchronous LLM calls inside the HTTP request. See
test_bulk_archive_writeback_task.py for coverage of that task's own
AI-best-effort / Copper-write behavior.

Follows the TestClient + dependency-override pattern used throughout
test_copper_writebacks.py / test_archive_no_reply_gate.py.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.tasks.bulk_archive_writeback import bulk_archive_writeback_task

client = TestClient(app)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns queued results for each execute() call in order. Records
    commit/rollback counts so tests can assert the per-lead isolation path
    actually rolls back a poisoned transaction before moving to the next
    lead."""

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _obj):
        pass


def _fake_lead(copper_id=None, copper_opportunity_id=None, status="assessed", lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        raw_copper_data={"tags": ["existing-tag"]},
        pitch_deck_text=None,
        status=status,
    )


async def _fake_current_user():
    return SimpleNamespace(email="reviewer@raed.vc", is_active=True)


@pytest.fixture
def override_auth():
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _override_db(results):
    session = _FakeSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


def _record_delay(monkeypatch):
    calls = []
    monkeypatch.setattr(bulk_archive_writeback_task, "delay", lambda lead_id: calls.append(lead_id))
    return calls


def test_bulk_archive_sets_all_leads_archived_and_dispatches_one_writeback_task_each(
    override_auth, monkeypatch
):
    calls = _record_delay(monkeypatch)
    leads = [_fake_lead(copper_id=str(i)) for i in range(3)]

    session = _override_db(list(leads))
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id) for lead in leads]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body == {"archived": 3, "copper_enqueued": 3, "failed": []}
    assert all(lead.status == "archived" for lead in leads)
    assert sorted(calls) == sorted(str(lead.id) for lead in leads)


def test_bulk_archive_no_rating_required_unlike_single_archive(override_auth, monkeypatch):
    """The single-lead archive-no-reply path 428s an unrated card; bulk
    archive must bypass that gate entirely while still dispatching the
    Copper write-back task. (Bulk archive never looks at the card's rating
    at all -- unlike archive-no-reply, it doesn't even fetch it.)"""
    calls = _record_delay(monkeypatch)
    lead = _fake_lead(copper_id="99")

    _override_db([lead])
    try:
        response = client.post("/api/v1/leads/bulk-archive", json={"lead_ids": [str(lead.id)]})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 1, "failed": []}
    assert lead.status == "archived"
    assert calls == [str(lead.id)]


def test_bulk_archive_one_failing_lead_does_not_abort_the_batch(override_auth, monkeypatch):
    """Per-lead isolation: a DB blow-up while processing one lead must not
    prevent the others in the same batch from archiving + dispatching."""
    calls = _record_delay(monkeypatch)
    good_lead_1 = _fake_lead(copper_id="1")
    bad_lead_id = uuid.uuid4()
    good_lead_2 = _fake_lead(copper_id="2")

    class _RaisingLogEvent:
        """log_event raises only for the bad lead -- simulates a mid-batch
        DB error on that one lead's write while leaving the others intact."""

        async def __call__(self, db, lead_id, event_type, payload=None):
            if lead_id == bad_lead_id:
                raise RuntimeError("db exploded")

    monkeypatch.setattr("app.routers.leads.log_event", _RaisingLogEvent())

    # bad_lead_id has no matching Lead row queued as "found" with that id --
    # simulate the DB error happening on a lead that IS found, so log_event's
    # id check fires.
    bad_lead = _fake_lead(copper_id="3", lead_id=bad_lead_id)

    session = _override_db([good_lead_1, bad_lead, good_lead_2])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(good_lead_1.id), str(bad_lead_id), str(good_lead_2.id)]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == 2
    assert body["copper_enqueued"] == 2
    assert len(body["failed"]) == 1
    assert body["failed"][0]["lead_id"] == str(bad_lead_id)
    assert "db exploded" in body["failed"][0]["error"]
    assert good_lead_1.status == "archived"
    assert good_lead_2.status == "archived"
    assert session.rollbacks == 1
    assert sorted(calls) == sorted([str(good_lead_1.id), str(good_lead_2.id)])


def test_bulk_archive_not_found_lead_is_isolated_as_a_failure(override_auth):
    lead = _fake_lead(copper_id=None)
    session = _override_db([lead, None])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id), str(uuid.uuid4())]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["error"] == "not_found"


def test_bulk_archive_invalid_lead_id_is_isolated_and_never_hits_the_db(override_auth):
    lead = _fake_lead(copper_id=None)
    session = _override_db([lead])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": ["not-a-uuid", str(lead.id)]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == 1
    assert body["failed"] == [{"lead_id": "not-a-uuid", "error": "invalid_lead_id"}]


def test_bulk_archive_already_archived_lead_is_idempotent_noop(override_auth):
    lead = _fake_lead(copper_id="42", status="archived")
    _override_db([lead])
    try:
        response = client.post("/api/v1/leads/bulk-archive", json={"lead_ids": [str(lead.id)]})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 0, "failed": []}


def test_bulk_archive_no_copper_id_skips_write_but_still_archives(override_auth, monkeypatch):
    calls = _record_delay(monkeypatch)
    lead = _fake_lead(copper_id=None)
    _override_db([lead])
    try:
        response = client.post("/api/v1/leads/bulk-archive", json={"lead_ids": [str(lead.id)]})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 0, "failed": []}
    assert lead.status == "archived"
    assert calls == []


def test_bulk_archive_already_converted_lead_skips_copper_write(override_auth, monkeypatch):
    calls = _record_delay(monkeypatch)
    lead = _fake_lead(copper_id="5", copper_opportunity_id="opp-1")
    _override_db([lead])
    try:
        response = client.post("/api/v1/leads/bulk-archive", json={"lead_ids": [str(lead.id)]})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 0, "failed": []}
    assert calls == []


def test_bulk_archive_non_admin_view_as_is_ignored_not_blocked(override_auth):
    """A non-admin's view_as is silently ignored (never honored), so it must
    NOT trigger the impersonation block -- only an honored view_as (admin)
    does that, exercised below."""
    lead = _fake_lead(copper_id=None)
    _override_db([lead])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive?view_as=someone-else@raed.vc",
            json={"lead_ids": [str(lead.id)]},
        )
    finally:
        _clear_db_override()

    assert response.status_code != 403


def test_bulk_archive_blocked_while_impersonating_as_admin(monkeypatch):
    from app.config import settings

    async def _fake_admin_user():
        return SimpleNamespace(email=settings.owner_email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_admin_user
    _override_db([])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive?view_as=someone-else@raed.vc",
            json={"lead_ids": [str(uuid.uuid4())]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        _clear_db_override()

    assert response.status_code == 403
    assert "Read-only while viewing another user's board" in response.json()["detail"]
