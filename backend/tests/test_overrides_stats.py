"""
Tests for GET /overrides/stats (issue #159): the raw aggregate-metrics query
must exclude assessment_overrides rows with reverted_at IS NOT NULL, the
same as /overrides/calibration -- an override undone via
POST /leads/{lead_id}/undo is a mistake, not a real AI-vs-team signal.

Same fake-session pattern as test_overrides_calibration.py -- no live
Postgres, a recording session captures the generated SQL text.
"""
from __future__ import annotations
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def all(self):
        return self._value if isinstance(self._value, list) else []


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)
        self.queries: list[str] = []

    async def execute(self, query, params=None):
        self.queries.append(str(query))
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


def test_non_admin_gets_403():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/overrides/stats")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403


def test_stats_excludes_reverted_rows_in_both_queries():
    totals_row = (10, 7, 3, 4, 2, 1, 1, 5)
    by_pair = []

    _auth_as(OWNER_EMAIL)
    session = _use_db([totals_row, by_pair])
    try:
        response = client.get("/api/v1/overrides/stats")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert len(session.queries) == 2
    for sql in session.queries:
        assert "reverted_at IS NULL" in sql

    body = response.json()
    assert body["total_rows"] == 10
    assert body["agreements"] == 7
