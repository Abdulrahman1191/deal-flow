"""
Tests for the owner-only duplicate-lead flagging surface (issue #180):
app/services/duplicates.py + app/routers/duplicates.py.

Split the same way test_dedup.py / test_close_duplicates_in_copper.py are:
pure clustering logic exercised directly (no DB), async service functions
exercised against a fake entity-routed AsyncSession (mirrors test_dedup.py's
_FakeSession -- no live Postgres needed), and the HTTP layer exercised with
fastapi.testclient.TestClient + dependency overrides (mirrors
test_multiuser_access.py / test_ops_endpoint.py).

This is a detect-and-flag mechanism only: these tests also lock in that
nothing here ever merges, archives, or auto-resolves a cluster on its own.
"""
from __future__ import annotations
import asyncio
import itertools
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.duplicate_dismissal import DuplicateDismissal
from app.models.lead import Lead
from app.routers import duplicates as duplicates_router
from app.services import duplicates
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"

# Each un-timestamped _lead() call gets its own hour, far outside the 5s
# same-second window -- so tests exercising the name/email signals alone
# don't accidentally also trip same_second just because two SimpleNamespaces
# were built moments apart.
_next_hour = itertools.count()


def _lead(
    company_name="Acme Deep Tech",
    owner_email="alice@raed.vc",
    copper_id=None,
    created_at=None,
    recipient_email=None,
    status="pending",
    lead_id=None,
):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        company_name=company_name,
        owner_email=owner_email,
        copper_id=copper_id,
        status=status,
        created_at=created_at or (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=next(_next_hour))),
        raw_copper_data={"recipient_email": recipient_email} if recipient_email else {},
    )


# ---------- pure clustering (find_duplicate_clusters) ----------


def test_two_leads_same_normalized_name_form_a_cluster():
    a = _lead(company_name="  Acme   Deep Tech ", owner_email="alice@raed.vc")
    b = _lead(company_name="acme deep tech", owner_email="bob@raed.vc")
    unrelated = _lead(company_name="Totally Different Co", owner_email="alice@raed.vc")

    clusters = duplicates.find_duplicate_clusters([a, b, unrelated])

    assert len(clusters) == 1
    cluster = clusters[0]
    assert {l.id for l in cluster["leads"]} == {a.id, b.id}
    assert cluster["match_reasons"] == ["name"]
    assert cluster["signal_count"] == 1


def test_two_leads_same_applicant_email_form_a_cluster():
    a = _lead(company_name="Acme Inc", recipient_email="Founder@Startup.com")
    b = _lead(company_name="Acme Incorporated", recipient_email="founder@startup.com")

    clusters = duplicates.find_duplicate_clusters([a, b])

    assert len(clusters) == 1
    assert clusters[0]["match_reasons"] == ["email"]


def test_cross_owner_duplicate_still_clusters():
    """The key case: the same company applied under two different partners."""
    a = _lead(company_name="Acme Co", owner_email="alice@raed.vc")
    b = _lead(company_name="Acme Co", owner_email="bob@raed.vc")

    clusters = duplicates.find_duplicate_clusters([a, b])

    assert len(clusters) == 1
    owners = {l.owner_email for l in clusters[0]["leads"]}
    assert owners == {"alice@raed.vc", "bob@raed.vc"}


def test_two_leads_created_within_5s_with_matching_name_form_a_cluster_with_same_second_reason():
    t0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    a = _lead(company_name="Twin Co", created_at=t0)
    b = _lead(company_name="Twin Co", created_at=t0 + timedelta(seconds=3))

    clusters = duplicates.find_duplicate_clusters([a, b])

    assert len(clusters) == 1
    assert clusters[0]["match_reasons"] == ["name", "same_second"]
    assert clusters[0]["signal_count"] == 2


def test_same_second_alone_without_name_or_email_match_is_not_a_duplicate():
    t0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    a = _lead(company_name="Alpha Robotics", created_at=t0)
    b = _lead(company_name="Beta Materials", created_at=t0 + timedelta(seconds=1))

    assert duplicates.find_duplicate_clusters([a, b]) == []


def test_more_than_5s_apart_does_not_get_same_second_reason():
    t0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    a = _lead(company_name="Twin Co", created_at=t0)
    b = _lead(company_name="Twin Co", created_at=t0 + timedelta(seconds=30))

    clusters = duplicates.find_duplicate_clusters([a, b])

    assert clusters[0]["match_reasons"] == ["name"]


def test_weak_email_signal_same_domain_and_similar_local_part():
    a = _lead(company_name="Alpha Co", recipient_email="jsmith@startup.com")
    b = _lead(company_name="Beta Co", recipient_email="jsmithh@startup.com")

    clusters = duplicates.find_duplicate_clusters([a, b])

    assert len(clusters) == 1
    assert clusters[0]["match_reasons"] == ["email"]


def test_different_domain_similar_local_part_does_not_match():
    a = _lead(company_name="Alpha Co", recipient_email="jsmith@startup.com")
    b = _lead(company_name="Beta Co", recipient_email="jsmith@othercompany.com")

    assert duplicates.find_duplicate_clusters([a, b]) == []


def test_unrelated_leads_do_not_cluster():
    a = _lead(company_name="Alpha Robotics")
    b = _lead(company_name="Beta Materials")

    assert duplicates.find_duplicate_clusters([a, b]) == []


def test_transitive_chain_unions_into_one_cluster():
    """A matches B on name; B matches C on email; A and C alone don't match
    -- all three must still land in the same cluster (union-find)."""
    a = _lead(company_name="Chain Co", recipient_email="a@one.com")
    b = _lead(company_name="Chain Co", recipient_email="b@two.com")
    c = _lead(company_name="Different Name Co", recipient_email="b@two.com")

    clusters = duplicates.find_duplicate_clusters([a, b, c])

    assert len(clusters) == 1
    assert {l.id for l in clusters[0]["leads"]} == {a.id, b.id, c.id}
    assert clusters[0]["match_reasons"] == ["email", "name"]


def test_clusters_sorted_newest_first():
    older_pair = [
        _lead(company_name="Old Co", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
        _lead(company_name="Old Co", created_at=datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc)),
    ]
    newer_pair = [
        _lead(company_name="New Co", created_at=datetime(2026, 6, 1, tzinfo=timezone.utc)),
        _lead(company_name="New Co", created_at=datetime(2026, 6, 1, 0, 0, 1, tzinfo=timezone.utc)),
    ]

    clusters = duplicates.find_duplicate_clusters([*older_pair, *newer_pair])

    assert len(clusters) == 2
    assert clusters[0]["leads"][0].company_name == "New Co"
    assert clusters[1]["leads"][0].company_name == "Old Co"
    # each cluster's own leads are newest-first too
    assert clusters[0]["leads"][0].created_at > clusters[0]["leads"][1].created_at


def test_cluster_key_is_sorted_and_order_independent():
    id1, id2 = uuid.uuid4(), uuid.uuid4()
    assert duplicates.cluster_key([id1, id2]) == duplicates.cluster_key([id2, id1])


# ---------- async service functions (fake AsyncSession, no live DB) ----------


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarOneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Routes execute() by query target entity (mirrors test_dedup.py /
    test_close_duplicates_in_copper.py): the leads list for `select(Lead)`;
    for `select(DuplicateDismissal...)`, a plain-column select (exactly one
    selected column named cluster_key) returns the dismissed-keys rows, a
    whole-entity select returns the canned existing-dismissal lookup."""

    def __init__(self, leads=None, dismissed_keys=None, existing_dismissal=None):
        self._leads = list(leads or [])
        self._dismissed_keys = list(dismissed_keys or [])
        self._existing_dismissal = existing_dismissal
        self.added: list = []
        self.committed = 0

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _ScalarsResult(self._leads)
        assert entity is DuplicateDismissal
        cols = list(query.selected_columns)
        if len(cols) == 1 and cols[0].name == "cluster_key":
            return _RowsResult([(k,) for k in self._dismissed_keys])
        return _ScalarOneResult(self._existing_dismissal)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        pass


def test_list_duplicate_clusters_excludes_dismissed_cluster():
    a = _lead(company_name="Dup Co", lead_id=uuid.uuid4())
    b = _lead(company_name="Dup Co", lead_id=uuid.uuid4())
    key = duplicates.cluster_key([a.id, b.id])

    session = _FakeSession(leads=[a, b], dismissed_keys=[key])
    result = asyncio.run(duplicates.list_duplicate_clusters(session))

    assert result == []


def test_list_duplicate_clusters_returns_non_dismissed_cluster():
    a = _lead(company_name="Dup Co", lead_id=uuid.uuid4())
    b = _lead(company_name="Dup Co", lead_id=uuid.uuid4())

    session = _FakeSession(leads=[a, b], dismissed_keys=["some-other-key"])
    result = asyncio.run(duplicates.list_duplicate_clusters(session))

    assert len(result) == 1


def test_dismiss_cluster_inserts_a_new_row():
    lead_ids = [uuid.uuid4(), uuid.uuid4()]
    session = _FakeSession(existing_dismissal=None)

    row = asyncio.run(duplicates.dismiss_cluster(session, lead_ids, dismissed_by=OWNER_EMAIL))

    assert isinstance(row, DuplicateDismissal)
    assert row.cluster_key == duplicates.cluster_key(lead_ids)
    assert row.dismissed_by == OWNER_EMAIL
    assert session.committed == 1
    assert session.added == [row]


def test_dismiss_cluster_is_idempotent_for_the_same_member_set():
    lead_ids = [uuid.uuid4(), uuid.uuid4()]
    existing = DuplicateDismissal(
        id=uuid.uuid4(), cluster_key=duplicates.cluster_key(lead_ids),
        lead_ids=[str(i) for i in lead_ids], dismissed_by=OWNER_EMAIL,
    )
    session = _FakeSession(existing_dismissal=existing)

    row = asyncio.run(duplicates.dismiss_cluster(session, lead_ids, dismissed_by=OWNER_EMAIL))

    assert row is existing
    assert session.committed == 0
    assert session.added == []


def test_a_new_lead_joining_a_dismissed_cluster_changes_its_key_and_reappears():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    dismissed_pair_key = duplicates.cluster_key([a, b])
    grown_cluster_key = duplicates.cluster_key([a, b, c])

    assert dismissed_pair_key != grown_cluster_key


# ---------- HTTP layer (TestClient, dependency overrides) ----------


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


def test_get_duplicates_requires_authentication():
    _clear_auth()
    response = client.get("/api/v1/duplicates")
    assert response.status_code == 401


def test_get_duplicates_forbidden_for_non_owner():
    _auth_as(COLLEAGUE_EMAIL)
    try:
        response = client.get("/api/v1/duplicates")
    finally:
        _clear_auth()
    assert response.status_code == 403


def test_get_duplicates_returns_clusters_for_owner(monkeypatch):
    lead = _lead(company_name="Acme Co", owner_email="alice@raed.vc", copper_id="C1",
                  recipient_email="founder@acme.test")
    twin = _lead(company_name="Acme Co", owner_email="bob@raed.vc", copper_id="C2")
    canned = [{"leads": [twin, lead], "match_reasons": ["name"], "signal_count": 1}]

    async def _fake_list(db):
        return canned

    monkeypatch.setattr(duplicates_router, "list_duplicate_clusters", _fake_list)

    _auth_as(OWNER_EMAIL)
    try:
        response = client.get("/api/v1/duplicates")
    finally:
        _clear_auth()

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["match_reasons"] == ["name"]
    assert body[0]["signal_count"] == 1
    ids = {row["id"] for row in body[0]["leads"]}
    assert ids == {str(lead.id), str(twin.id)}
    by_id = {row["id"]: row for row in body[0]["leads"]}
    assert by_id[str(lead.id)]["applicant_email"] == "founder@acme.test"
    assert by_id[str(lead.id)]["owner_email"] == "alice@raed.vc"
    assert by_id[str(twin.id)]["owner_email"] == "bob@raed.vc"


def test_dismiss_forbidden_for_non_owner():
    _auth_as(COLLEAGUE_EMAIL)
    try:
        response = client.post(
            "/api/v1/duplicates/dismiss",
            json={"lead_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
        )
    finally:
        _clear_auth()
    assert response.status_code == 403


def test_dismiss_requires_at_least_two_lead_ids():
    _auth_as(OWNER_EMAIL)
    try:
        response = client.post("/api/v1/duplicates/dismiss", json={"lead_ids": [str(uuid.uuid4())]})
    finally:
        _clear_auth()
    assert response.status_code == 422


def test_dismiss_calls_service_with_acting_owner_email(monkeypatch):
    lead_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    captured = {}

    async def _fake_dismiss(db, ids, dismissed_by):
        captured["ids"] = [str(i) for i in ids]
        captured["dismissed_by"] = dismissed_by
        return DuplicateDismissal(
            id=uuid.uuid4(), cluster_key=duplicates.cluster_key(ids),
            lead_ids=[str(i) for i in ids], dismissed_by=dismissed_by,
            dismissed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(duplicates_router, "dismiss_cluster", _fake_dismiss)

    _auth_as(OWNER_EMAIL)
    try:
        response = client.post("/api/v1/duplicates/dismiss", json={"lead_ids": lead_ids})
    finally:
        _clear_auth()

    assert response.status_code == 200
    assert captured["dismissed_by"] == OWNER_EMAIL
    assert sorted(captured["ids"]) == sorted(lead_ids)


def test_dismissed_cluster_no_longer_appears_end_to_end():
    """dismiss then re-list against the same underlying leads -- the
    dismissed cluster must not come back."""
    a = _lead(company_name="Dup Co", lead_id=uuid.uuid4())
    b = _lead(company_name="Dup Co", lead_id=uuid.uuid4())

    dismiss_session = _FakeSession(existing_dismissal=None)
    row = asyncio.run(duplicates.dismiss_cluster(dismiss_session, [a.id, b.id], dismissed_by=OWNER_EMAIL))

    list_session = _FakeSession(leads=[a, b], dismissed_keys=[row.cluster_key])
    result = asyncio.run(duplicates.list_duplicate_clusters(list_session))

    assert result == []


def test_reassign_forbidden_for_non_owner():
    _auth_as(COLLEAGUE_EMAIL)
    try:
        response = client.post(
            "/api/v1/duplicates/reassign",
            json={"lead_id": str(uuid.uuid4()), "owner_email": "bob@raed.vc"},
        )
    finally:
        _clear_auth()
    assert response.status_code == 403


class _ReassignResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ReassignSession:
    def __init__(self, lead, target_user=None):
        self._lead = lead
        self._target_user = target_user
        self.added: list = []
        self.committed = 0

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _ReassignResult(self._lead)
        return _ReassignResult(self._target_user)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        pass


def test_reassign_not_found_returns_404():
    _auth_as(OWNER_EMAIL)
    _use_db(_ReassignSession(lead=None))
    try:
        response = client.post(
            "/api/v1/duplicates/reassign",
            json={"lead_id": str(uuid.uuid4()), "owner_email": "bob@raed.vc"},
        )
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 404


def test_reassign_updates_owner_and_best_effort_pushes_to_copper(monkeypatch):
    lead = _lead(company_name="Acme Co", owner_email="alice@raed.vc", copper_id="C1")
    target_user = SimpleNamespace(email="bob@raed.vc", copper_user_id=42)
    session = _ReassignSession(lead=lead, target_user=target_user)

    pushed = {}
    monkeypatch.setattr(
        duplicates_router.copper_writer, "push_assignee",
        lambda copper_id, assignee_id: pushed.update(copper_id=copper_id, assignee_id=assignee_id),
    )

    _auth_as(OWNER_EMAIL)
    _use_db(session)
    try:
        response = client.post(
            "/api/v1/duplicates/reassign",
            json={"lead_id": str(lead.id), "owner_email": "Bob@Raed.vc"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["from_owner"] == "alice@raed.vc"
    assert body["to_owner"] == "bob@raed.vc"
    assert body["copper_push_enqueued"] is True
    assert lead.owner_email == "bob@raed.vc"
    assert pushed == {"copper_id": "C1", "assignee_id": 42}
    assert session.committed == 1


def test_reassign_never_touches_lead_status_or_deletes_anything():
    """Detect-and-flag guarantee: reassigning must never archive/resolve the
    lead -- only owner_email changes."""
    lead = _lead(company_name="Acme Co", owner_email="alice@raed.vc", copper_id=None, status="pending")
    session = _ReassignSession(lead=lead, target_user=None)

    _auth_as(OWNER_EMAIL)
    _use_db(session)
    try:
        response = client.post(
            "/api/v1/duplicates/reassign",
            json={"lead_id": str(lead.id), "owner_email": "carol@raed.vc"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert lead.status == "pending"
    assert response.json()["copper_push_enqueued"] is False
