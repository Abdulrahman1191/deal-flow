"""
Tests for bulk-archive batch undo (issue #159, extending #141's bulk-archive
and #153's undo mechanism):
  - POST /leads/bulk-archive stamps every lead_action_log row it writes with
    a shared batch_id.
  - bulk_archive_writeback_task patches its Copper outbox id back onto the
    matching lead_action_log row once the (async, per-lead) Copper write-back
    completes, so a batch undo can still cancel a still-pending write.
  - POST /leads/bulk-archive/{batch_id}/undo reverses every not-yet-undone
    row in the batch, with per-lead isolation mirroring bulk_archive_leads
    itself.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import copper_writer, undo as undo_service
from app.services.auth import get_current_user
from app.tasks import bulk_archive_writeback
from app.tasks.bulk_archive_writeback import bulk_archive_writeback_task

client = TestClient(app)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeResult:
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
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

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


def _fake_action(lead_id, batch_id, prior_status="assessed", prior_tags=None, copper_outbox_id=None,
                  undone_at=None, created_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        card_id=None,
        batch_id=batch_id,
        action_type=undo_service.ACTION_BULK_ARCHIVE,
        actor_email="reviewer@raed.vc",
        prior_state={"status": prior_status, "copper_id": None, "copper_tags": prior_tags or []},
        email_sent=False,
        copper_outbox_id=copper_outbox_id,
        undone_at=undone_at,
        created_at=created_at or datetime.now(timezone.utc),
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


def _override_db(results) -> _FakeSession:
    session = _FakeSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 1. bulk_archive_leads stamps a shared batch_id
# ---------------------------------------------------------------------------

def test_bulk_archive_writes_action_log_rows_with_shared_batch_id(override_auth, monkeypatch):
    delay_calls = []
    monkeypatch.setattr(
        bulk_archive_writeback_task, "delay",
        lambda lead_id, batch_id=None: delay_calls.append((lead_id, batch_id)),
    )

    leads = [_fake_lead(copper_id=str(i)) for i in range(2)]
    session = _override_db(list(leads))
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id) for lead in leads]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200

    from app.models.lead_action_log import LeadActionLog
    logged = [o for o in session.added if isinstance(o, LeadActionLog)]
    assert len(logged) == 2
    assert all(row.action_type == undo_service.ACTION_BULK_ARCHIVE for row in logged)
    assert {row.lead_id for row in logged} == {lead.id for lead in leads}

    batch_ids = {row.batch_id for row in logged}
    assert len(batch_ids) == 1
    assert None not in batch_ids

    # The same batch_id is passed through to the per-lead Copper write-back
    # dispatch, as a string.
    dispatched_batch_ids = {batch_id for _lead_id, batch_id in delay_calls}
    assert dispatched_batch_ids == {str(next(iter(batch_ids)))}


def test_bulk_archive_already_archived_lead_gets_no_action_log_row(override_auth):
    lead = _fake_lead(copper_id="42", status="archived")
    session = _override_db([lead])
    try:
        response = client.post("/api/v1/leads/bulk-archive", json={"lead_ids": [str(lead.id)]})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    from app.models.lead_action_log import LeadActionLog
    assert [o for o in session.added if isinstance(o, LeadActionLog)] == []


# ---------------------------------------------------------------------------
# 2. bulk_archive_writeback_task patches the outbox id onto the batch row
# ---------------------------------------------------------------------------

class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeActionLogTaskSession:
    def __init__(self, lead, card=None, action=None):
        self.lead = lead
        self.card = card
        self.action = action
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        from app.models.lead import Lead
        from app.models.assessment import AssessmentCard
        from app.models.lead_action_log import LeadActionLog

        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _FakeScalarResult(self.lead)
        if entity is AssessmentCard:
            return _FakeScalarResult(self.card)
        assert entity is LeadActionLog
        return _FakeScalarResult(self.action)

    async def commit(self):
        self.committed += 1


def _fake_task_lead(copper_id="7", copper_opportunity_id=None, lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        raw_copper_data={"tags": ["existing-tag"]},
    )


def test_writeback_task_patches_outbox_id_onto_batch_log_row(monkeypatch):
    lead = _fake_task_lead()
    action = SimpleNamespace(copper_outbox_id=None)
    session = _FakeActionLogTaskSession(lead, card=None, action=action)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(copper_writer, "archive_in_copper", lambda *a, **k: "new-outbox-id")

    batch_id = str(uuid.uuid4())
    result = asyncio.run(bulk_archive_writeback._run(str(lead.id), batch_id))

    assert result == {"lead_id": str(lead.id), "status": "written_back"}
    assert action.copper_outbox_id == "new-outbox-id"
    assert session.committed == 1


def test_writeback_task_without_batch_id_never_queries_action_log(monkeypatch):
    """Backward compatible: a caller that doesn't pass batch_id (there are
    none left in this codebase, but defends the default) never touches
    lead_action_log at all."""
    lead = _fake_task_lead()
    session = _FakeActionLogTaskSession(lead, card=None, action=None)
    monkeypatch.setattr(bulk_archive_writeback, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(copper_writer, "archive_in_copper", lambda *a, **k: "new-outbox-id")

    result = asyncio.run(bulk_archive_writeback._run(str(lead.id)))

    assert result == {"lead_id": str(lead.id), "status": "written_back"}
    assert session.committed == 0


# ---------------------------------------------------------------------------
# 3. POST /leads/bulk-archive/{batch_id}/undo
# ---------------------------------------------------------------------------

def test_undo_bulk_archive_batch_reverses_each_lead(override_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        copper_writer, "reverse_archive_in_copper",
        lambda copper_id, prior_tags, pending_outbox_id=None: calls.append(copper_id) or "new-outbox",
    )

    batch_id = uuid.uuid4()
    lead1 = _fake_lead(status="archived", copper_id="1")
    lead2 = _fake_lead(status="archived", copper_id="2")
    action1 = _fake_action(lead1.id, batch_id, prior_status="assessed")
    action2 = _fake_action(lead2.id, batch_id, prior_status="pending")

    results = [
        [action1, action2],  # the batch query (scalars().all())
        lead1,                # lead1 lookup
        None,                 # AssessmentOverride lookup inside undo_action for lead1
        lead2,                # lead2 lookup
        None,                 # AssessmentOverride lookup inside undo_action for lead2
    ]
    _override_db(results)
    try:
        response = client.post(f"/api/v1/leads/bulk-archive/{batch_id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"undone": 2, "already_undone": 0, "failed": []}
    assert lead1.status == "assessed"
    assert lead2.status == "pending"
    assert action1.undone_at is not None
    assert action2.undone_at is not None
    assert calls == ["1", "2"]


def test_undo_bulk_archive_batch_404_when_not_found(override_auth):
    _override_db([[]])
    try:
        response = client.post(f"/api/v1/leads/bulk-archive/{uuid.uuid4()}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 404


def test_undo_bulk_archive_batch_400_on_invalid_id(override_auth):
    response = client.post("/api/v1/leads/bulk-archive/not-a-uuid/undo")
    assert response.status_code == 400


def test_undo_bulk_archive_batch_isolates_per_lead_conflicts(override_auth, monkeypatch):
    """One lead converted to a Copper Opportunity since, one lead's status
    already drifted, one already undone, one succeeds -- none of these block
    each other."""
    monkeypatch.setattr(copper_writer, "reverse_archive_in_copper", lambda *a, **k: "new-outbox")

    batch_id = uuid.uuid4()
    good_lead = _fake_lead(status="archived", copper_id="1")
    converted_lead = _fake_lead(status="archived", copper_id="2", copper_opportunity_id="opp-1")
    drifted_lead = _fake_lead(status="approved", copper_id="3")

    good_action = _fake_action(good_lead.id, batch_id, prior_status="assessed")
    converted_action = _fake_action(converted_lead.id, batch_id, prior_status="assessed")
    drifted_action = _fake_action(drifted_lead.id, batch_id, prior_status="assessed")
    already_undone_action = _fake_action(
        uuid.uuid4(), batch_id, prior_status="assessed", undone_at=datetime.now(timezone.utc),
    )
    already_undone_lead = _fake_lead(status="assessed", lead_id=already_undone_action.lead_id)

    actions = [good_action, converted_action, drifted_action, already_undone_action]
    results = [
        actions,
        good_lead, None,       # good: lead lookup + override lookup
        converted_lead,        # converted: lead lookup only (fails before override lookup)
        drifted_lead,          # drifted: lead lookup only
        already_undone_lead,   # already-undone: lead lookup only
    ]
    _override_db(results)
    try:
        response = client.post(f"/api/v1/leads/bulk-archive/{batch_id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["undone"] == 1
    assert body["already_undone"] == 1
    assert {f["lead_id"] for f in body["failed"]} == {str(converted_lead.id), str(drifted_lead.id)}
    errors = {f["lead_id"]: f["error"] for f in body["failed"]}
    assert errors[str(converted_lead.id)] == "converted_to_opportunity"
    assert errors[str(drifted_lead.id)] == "status_drifted"
    assert good_lead.status == "assessed"


def test_undo_bulk_archive_batch_enforces_undo_window(override_auth, monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "undo_window_hours", 72)
    monkeypatch.setattr(copper_writer, "reverse_archive_in_copper", lambda *a, **k: "new-outbox")

    batch_id = uuid.uuid4()
    stale_action = _fake_action(
        uuid.uuid4(), batch_id, prior_status="assessed",
        created_at=datetime.now(timezone.utc) - timedelta(hours=200),
    )
    stale_lead = _fake_lead(status="archived", lead_id=stale_action.lead_id)

    _override_db([[stale_action], stale_lead])
    try:
        response = client.post(f"/api/v1/leads/bulk-archive/{batch_id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["undone"] == 0
    assert body["failed"] == [{"lead_id": str(stale_lead.id), "error": "undo_window_expired"}]
