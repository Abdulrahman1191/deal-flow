"""
Tests for admin-only bulk lead reassignment (issue #194):
app/services/bulk_reassign.py + POST /leads/reassign/preview + POST
/leads/reassign.

The central correctness requirement this locks in: reconcile_ownership_task
(app/tasks/reconcile_ownership.py) snaps owner_email back to Copper's current
assignee_id every 5 minutes, so a reassignment that can't also be pushed to
Copper would silently revert and look like data loss. Execute therefore
refuses upfront (400, nothing written) if any target lacks a copper_user_id,
and otherwise enqueues copper_writer.push_assignee (outbox-backed) per lead.

Follows the TestClient + dependency-override + entity-routed fake session
pattern used throughout test_duplicates.py / test_bulk_archive.py -- no live
Postgres needed.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.lead import Lead
from app.models.user import User
from app.routers import leads as leads_router
from app.services import bulk_reassign
from app.services.auth import get_current_user
from app.services.events import EVENT_REASSIGNED

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"
FROM_OWNER = "yomna@raed.vc"
PREVIEW_URL = "/api/v1/leads/reassign/preview"
EXECUTE_URL = "/api/v1/leads/reassign"


def _uid(n: int) -> uuid.UUID:
    """Deterministic lead ids so round-robin assignment order is predictable
    in tests -- the feature's whole "sorted by lead id" determinism guarantee
    would otherwise be untestable against random uuid4()s."""
    return uuid.UUID(int=n)


def _lead(
    n: int,
    owner_email: str = FROM_OWNER,
    status: str = "assessed",
    copper_id: str | None = "unset",
    copper_opportunity_id: str | None = None,
    bucket: str | None = None,
    user_override: str | None = None,
    sent_at=None,
):
    assessment = None
    if bucket is not None or user_override is not None or sent_at is not None:
        assessment = SimpleNamespace(bucket=bucket, user_override=user_override, sent_at=sent_at)
    return SimpleNamespace(
        id=_uid(n),
        owner_email=owner_email,
        status=status,
        copper_id=f"copper-{n}" if copper_id == "unset" else copper_id,
        copper_opportunity_id=copper_opportunity_id,
        assessment=assessment,
    )


def _user(email: str, copper_user_id: int | None):
    return SimpleNamespace(email=email, copper_user_id=copper_user_id)


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Routes execute() by query target entity (mirrors test_duplicates.py):
    the leads list for `select(Lead)`, the users list for `select(User)`.
    Records commit/rollback counts and added rows (LeadEvents) so tests can
    assert write behavior without a live DB."""

    def __init__(self, leads=None, users=None):
        self._leads = list(leads or [])
        self._users = list(users or [])
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _ScalarsResult(self._leads)
        assert entity is User
        return _ScalarsResult(self._users)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        pass


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(session):
    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


# ---------- preview ----------


def test_preview_returns_count_and_per_target_split_and_writes_nothing():
    leads = [_lead(i) for i in range(1, 5)]
    session = _FakeSession(leads=leads)

    _auth_as(OWNER_EMAIL)
    _use_db(session)
    try:
        response = client.post(
            PREVIEW_URL,
            json={"from_owner": FROM_OWNER, "to_owners": ["waleed@raed.vc", "uday@raed.vc"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    assert body["by_target"] == {"waleed@raed.vc": 2, "uday@raed.vc": 2}
    assert body["by_status"] == {"assessed": 4}
    assert body["by_bucket"] == {"unscored": 4}
    assert session.commits == 0
    assert session.added == []
    assert all(lead.owner_email == FROM_OWNER for lead in leads)


def test_preview_forbidden_for_non_admin():
    _auth_as(COLLEAGUE_EMAIL)
    try:
        response = client.post(
            PREVIEW_URL, json={"from_owner": FROM_OWNER, "to_owners": ["uday@raed.vc"]},
        )
    finally:
        _clear_auth()
    assert response.status_code == 403


# ---------- two-target determinism ----------


def test_two_targets_split_evenly_and_deterministically_same_input_twice():
    leads = [_lead(i) for i in range(1, 5)]
    to_owners = ["waleed@raed.vc", "uday@raed.vc"]

    sorted_leads = sorted(leads, key=lambda l: l.id)
    assignments = bulk_reassign.round_robin_assignments(sorted_leads, to_owners)
    assert assignments[_uid(1)] == "waleed@raed.vc"
    assert assignments[_uid(2)] == "uday@raed.vc"
    assert assignments[_uid(3)] == "waleed@raed.vc"
    assert assignments[_uid(4)] == "uday@raed.vc"

    _auth_as(OWNER_EMAIL)
    try:
        _use_db(_FakeSession(leads=leads))
        first = client.post(PREVIEW_URL, json={"from_owner": FROM_OWNER, "to_owners": to_owners})
        _clear_db()

        _use_db(_FakeSession(leads=leads))
        second = client.post(PREVIEW_URL, json={"from_owner": FROM_OWNER, "to_owners": to_owners})
    finally:
        _clear_auth()
        _clear_db()

    assert first.json()["by_target"] == second.json()["by_target"] == {
        "waleed@raed.vc": 2, "uday@raed.vc": 2,
    }


# ---------- execute: happy path ----------


def test_execute_moves_every_lead_writes_events_with_batch_id_and_enqueues_outbox_per_lead(monkeypatch):
    leads = [_lead(i) for i in range(1, 4)]
    target = _user("waleed@raed.vc", copper_user_id=1156618)
    session = _FakeSession(leads=leads, users=[target])

    pushed = []
    monkeypatch.setattr(
        leads_router.copper_writer, "push_assignee",
        lambda copper_id, assignee_id: pushed.append((copper_id, assignee_id)),
    )

    _auth_as(OWNER_EMAIL)
    _use_db(session)
    try:
        response = client.post(
            EXECUTE_URL,
            json={"from_owner": FROM_OWNER, "to_owners": ["waleed@raed.vc"], "confirm_count": 3},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["moved"] == 3
    assert body["failed"] == []
    assert body["by_target"] == {"waleed@raed.vc": 3}
    batch_id = body["batch_id"]

    assert all(lead.owner_email == "waleed@raed.vc" for lead in leads)
    assert sorted(pushed) == sorted((f"copper-{i}", 1156618) for i in range(1, 4))

    assert len(session.added) == 3
    for event in session.added:
        assert event.event_type == EVENT_REASSIGNED
        assert event.payload["from_owner"] == FROM_OWNER
        assert event.payload["to_owner"] == "waleed@raed.vc"
        assert event.payload["source"] == "bulk_reassign"
        assert event.payload["by"] == OWNER_EMAIL
        assert event.payload["batch_id"] == batch_id
    assert session.commits == 3


def test_execute_forbidden_for_non_admin():
    _auth_as(COLLEAGUE_EMAIL)
    try:
        response = client.post(
            EXECUTE_URL,
            json={"from_owner": FROM_OWNER, "to_owners": ["uday@raed.vc"], "confirm_count": 0},
        )
    finally:
        _clear_auth()
    assert response.status_code == 403


# ---------- execute: refusals (nothing written) ----------


def test_target_without_copper_user_id_returns_400_and_writes_nothing(monkeypatch):
    lead = _lead(1)
    users = [_user("waleed@raed.vc", copper_user_id=None), _user("uday@raed.vc", copper_user_id=1150537)]
    session = _FakeSession(leads=[lead], users=users)

    pushed = []
    monkeypatch.setattr(
        leads_router.copper_writer, "push_assignee",
        lambda copper_id, assignee_id: pushed.append((copper_id, assignee_id)),
    )

    _auth_as(OWNER_EMAIL)
    _use_db(session)
    try:
        response = client.post(
            EXECUTE_URL,
            json={
                "from_owner": FROM_OWNER,
                "to_owners": ["waleed@raed.vc", "uday@raed.vc"],
                "confirm_count": 1,
            },
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert "waleed@raed.vc" in response.json()["detail"]
    assert session.commits == 0
    assert session.added == []
    assert pushed == []
    assert lead.owner_email == FROM_OWNER


def test_confirm_count_mismatch_returns_409_and_writes_nothing(monkeypatch):
    leads = [_lead(1), _lead(2)]
    target = _user("waleed@raed.vc", copper_user_id=1156618)
    session = _FakeSession(leads=leads, users=[target])

    pushed = []
    monkeypatch.setattr(
        leads_router.copper_writer, "push_assignee",
        lambda copper_id, assignee_id: pushed.append((copper_id, assignee_id)),
    )

    _auth_as(OWNER_EMAIL)
    _use_db(session)
    try:
        response = client.post(
            EXECUTE_URL,
            json={"from_owner": FROM_OWNER, "to_owners": ["waleed@raed.vc"], "confirm_count": 99},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 409
    assert session.commits == 0
    assert session.added == []
    assert pushed == []
    assert all(lead.owner_email == FROM_OWNER for lead in leads)


# ---------- filters ----------


def test_archived_converted_and_sent_leads_excluded_by_default_included_with_flag():
    active = _lead(1, status="assessed")
    archived = _lead(2, status="archived")
    converted = _lead(3, status="assessed", copper_opportunity_id="opp-1")
    sent = _lead(4, status="assessed", sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    leads = [active, archived, converted, sent]

    _auth_as(OWNER_EMAIL)
    try:
        _use_db(_FakeSession(leads=leads))
        default_resp = client.post(
            PREVIEW_URL, json={"from_owner": FROM_OWNER, "to_owners": ["waleed@raed.vc"]},
        )
        _clear_db()

        _use_db(_FakeSession(leads=leads))
        included_resp = client.post(
            PREVIEW_URL,
            json={
                "from_owner": FROM_OWNER,
                "to_owners": ["waleed@raed.vc"],
                "include_converted": True,
            },
        )
    finally:
        _clear_auth()
        _clear_db()

    assert default_resp.json()["count"] == 1  # only `active`
    # archived is never movable -- include_converted only restores
    # converted/sent, not archived.
    assert included_resp.json()["count"] == 3  # active, converted, sent


def test_bucket_filter_narrows_to_matching_effective_bucket():
    yes_lead = _lead(1, bucket="YES")
    maybe_lead = _lead(2, bucket="MAYBE")
    overridden_lead = _lead(3, bucket="MAYBE", user_override="YES")
    leads = [yes_lead, maybe_lead, overridden_lead]

    _auth_as(OWNER_EMAIL)
    _use_db(_FakeSession(leads=leads))
    try:
        response = client.post(
            PREVIEW_URL,
            json={"from_owner": FROM_OWNER, "to_owners": ["waleed@raed.vc"], "bucket": "YES"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.json()["count"] == 2  # yes_lead + overridden_lead (override wins)


# ---------- per-lead isolation ----------


def test_mid_batch_failure_leaves_rest_applied_and_names_failure(monkeypatch):
    good_1 = _lead(1)
    bad = _lead(2)
    good_2 = _lead(3)
    leads = [good_1, bad, good_2]
    target = _user("waleed@raed.vc", copper_user_id=1156618)
    session = _FakeSession(leads=leads, users=[target])

    pushed = []
    monkeypatch.setattr(
        leads_router.copper_writer, "push_assignee",
        lambda copper_id, assignee_id: pushed.append((copper_id, assignee_id)),
    )

    class _RaisingLogEvent:
        async def __call__(self, db, lead_id, event_type, payload=None):
            if lead_id == bad.id:
                raise RuntimeError("db exploded")

    monkeypatch.setattr(leads_router, "log_event", _RaisingLogEvent())

    _auth_as(OWNER_EMAIL)
    _use_db(session)
    try:
        response = client.post(
            EXECUTE_URL,
            json={"from_owner": FROM_OWNER, "to_owners": ["waleed@raed.vc"], "confirm_count": 3},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["moved"] == 2
    assert len(body["failed"]) == 1
    assert body["failed"][0]["lead_id"] == str(bad.id)
    assert "db exploded" in body["failed"][0]["error"]
    assert good_1.owner_email == "waleed@raed.vc"
    assert good_2.owner_email == "waleed@raed.vc"
    assert session.rollbacks == 1
    assert sorted(pushed) == sorted([("copper-1", 1156618), ("copper-3", 1156618)])
