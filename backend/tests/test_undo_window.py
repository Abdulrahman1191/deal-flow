"""
Tests for the undo time window (issue #159): `settings.undo_window_hours`
(default 72) bounds POST /leads/{lead_id}/undo and the bulk-archive
batch-undo endpoint. An action older than the window is refused with a
clear message rather than silently doing nothing or clobbering unrelated
state that changed in the meantime. The separate Archive-page restore flow
is deliberately not bound by this -- see the config comment.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.services import undo as undo_service
from app.services.auth import get_current_user

client = TestClient(app)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj):
        pass

    async def commit(self):
        pass


def _fake_lead(status="archived", copper_id=None, copper_opportunity_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        raw_copper_data=None,
        pitch_deck_text=None,
        status=status,
    )


def _fake_action(lead_id, created_at):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        card_id=None,
        action_type=undo_service.ACTION_ARCHIVE_NO_REPLY,
        actor_email="reviewer@raed.vc",
        prior_state={"status": "assessed", "copper_id": None, "copper_tags": []},
        email_sent=False,
        copper_outbox_id=None,
        undone_at=None,
        created_at=created_at,
    )


@pytest.fixture
def override_auth():
    async def _fake_current_user():
        return SimpleNamespace(email="reviewer@raed.vc", is_active=True)

    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _override_db(results):
    async def _fake_get_db():
        yield _FakeSession(results)

    app.dependency_overrides[get_db] = _fake_get_db


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


# ---------- pure function: is_within_undo_window ----------

def test_is_within_undo_window_true_for_recent_action(monkeypatch):
    monkeypatch.setattr(settings, "undo_window_hours", 72)
    action = _fake_action(uuid.uuid4(), created_at=datetime.now(timezone.utc) - timedelta(hours=1))
    assert undo_service.is_within_undo_window(action) is True


def test_is_within_undo_window_false_for_old_action(monkeypatch):
    monkeypatch.setattr(settings, "undo_window_hours", 72)
    action = _fake_action(uuid.uuid4(), created_at=datetime.now(timezone.utc) - timedelta(hours=73))
    assert undo_service.is_within_undo_window(action) is False


def test_is_within_undo_window_respects_custom_setting(monkeypatch):
    monkeypatch.setattr(settings, "undo_window_hours", 1)
    action = _fake_action(uuid.uuid4(), created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert undo_service.is_within_undo_window(action) is False


def test_undo_window_hours_defaults_to_72():
    from app.config import Settings

    assert Settings.model_fields["undo_window_hours"].default == 72


# ---------- POST /leads/{lead_id}/undo refuses stale actions ----------

def test_undo_endpoint_refuses_action_older_than_window(override_auth, monkeypatch):
    monkeypatch.setattr(settings, "undo_window_hours", 72)

    lead = _fake_lead(status="archived")
    action = _fake_action(lead.id, created_at=datetime.now(timezone.utc) - timedelta(hours=200))

    _override_db([lead, action])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "too old to undo" in response.json()["detail"].lower()
    assert lead.status == "archived"  # untouched


def test_undo_endpoint_allows_action_within_window(override_auth, monkeypatch):
    monkeypatch.setattr(settings, "undo_window_hours", 72)

    lead = _fake_lead(status="archived")
    action = _fake_action(lead.id, created_at=datetime.now(timezone.utc) - timedelta(hours=1))

    _override_db([lead, action, None])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/undo")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["status"] == "undone"
