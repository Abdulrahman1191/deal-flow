"""
Tests for the board filter change in issue #67: `awaiting_deck` leads are
excluded from the default kanban view (alongside archived/approved) but are
fetchable via `?status=awaiting_deck`, scoped to the acting user exactly like
every other list_leads query (including under admin view_as -- already
covered generically by test_view_as.py's `test_admin_reads_colleagues_leads_via_view_as`,
since the owner_email scoping code path is unchanged here).

Mirrors the _RecordingSession pattern in test_multiuser_access.py so the
bound query params can be inspected -- this is what proves the `notin_` list
actually contains "awaiting_deck" rather than just trusting canned fixture
data.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = "owner@raed.vc"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else []


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)
        self.queries: list[dict] = []

    async def execute(self, query):
        try:
            params = dict(query.compile().params)
        except Exception:
            params = {}
        self.queries.append(params)
        return _FakeResult(self._results.pop(0))


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(results) -> _RecordingSession:
    session = _RecordingSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def _fake_lead_row(owner_email: str, status: str, company_name: str = "Acme Deep Tech"):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        copper_id=None,
        owner_email=owner_email,
        company_name=company_name,
        website=None,
        description=None,
        stage=None,
        region=None,
        founder_names=None,
        linkedin_urls=None,
        company_linkedin_url=None,
        pitch_deck_filename=None,
        pitch_deck_ingested_at=None,
        pitch_deck_drive_id=None,
        status=status,
        created_at=now,
        updated_at=now,
        assessment=None,
    )


def test_default_list_excludes_awaiting_deck():
    board_lead = _fake_lead_row(OWNER_EMAIL, "pending")
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [board_lead]])
    try:
        response = client.get("/api/v1/leads")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert [item["status"] for item in body["items"]] == ["pending"]

    # The notin_ filter must include "archived", "approved", AND
    # "awaiting_deck" -- this is the regression check: a dropped
    # "awaiting_deck" entry would still pass the fixture-based assertion
    # above but would show up here.
    for call in range(len(session.queries)):
        values = _bound_values(session, call)
        assert "archived" in values
        assert "approved" in values
        assert "awaiting_deck" in values


def test_explicit_status_query_returns_awaiting_deck_leads_scoped_to_owner():
    parked_lead = _fake_lead_row(OWNER_EMAIL, "awaiting_deck")
    _auth_as(OWNER_EMAIL)
    session = _use_db([1, [parked_lead]])
    try:
        response = client.get("/api/v1/leads?status=awaiting_deck")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "awaiting_deck"
    assert body["items"][0]["owner_email"] == OWNER_EMAIL

    for call in range(len(session.queries)):
        values = _bound_values(session, call)
        assert OWNER_EMAIL in values
        assert "awaiting_deck" in values


def _bound_values(session: _RecordingSession, call_index: int) -> list:
    """Flattened bound values for a query call. `notin_`/`in_` compile to a
    single expanding bindparam whose value is the whole list (e.g.
    ["archived", "approved", "awaiting_deck"]) rather than one bindparam per
    item, so list/tuple values are flattened before searching."""
    flat = []
    for value in session.queries[call_index].values():
        if isinstance(value, (list, tuple, set)):
            flat.extend(value)
        else:
            flat.append(value)
    return flat
