"""
Tests for GET /overrides/calibration — the calibration-dashboard backend
(issue #104): agreement-over-time, per-partner rating profiles, and the
articulation-rate data-quality signal.

Same fake-session pattern as test_view_as.py / test_multiuser_access.py: no
live Postgres in this suite. A recording session returns canned rows shaped
exactly like the tuples Postgres would hand back for each aggregate query, so
these tests exercise the router's math/wiring rather than the SQL engine.
"""
from __future__ import annotations
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.routers.overrides import _rate
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
        self.params: list[dict] = []

    async def execute(self, query, params=None):
        self.queries.append(str(query))
        self.params.append(params or {})
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


# ---------- pure math: _rate ----------


def test_rate_computes_fraction():
    assert _rate(3, 4) == 0.75


def test_rate_rounds_to_four_places():
    assert _rate(1, 3) == round(1 / 3, 4)


def test_rate_none_when_denominator_zero():
    assert _rate(0, 0) is None


def test_rate_zero_when_numerator_zero():
    assert _rate(0, 5) == 0.0


# ---------- endpoint: auth gate ----------


def test_non_admin_gets_403():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    try:
        response = client.get("/api/v1/overrides/calibration")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 403


# ---------- endpoint: full response wiring ----------


def _week(offset_days: int) -> datetime:
    return datetime(2026, 7, 6, tzinfo=timezone.utc)  # a Monday


def test_calibration_endpoint_assembles_all_sections():
    overall_row = (10, 7, 3)  # total, agreements, disagreements
    weekly_rows = [
        (_week(0), 4, 3),
        (_week(7), 6, 4),
    ]
    partner_rows = [
        ("waleed@raed.vc", 6, 5, 2, 3, 1, 4),
        ("yomna@raed.vc", 4, 2, 0, 4, 0, 1),
    ]
    pair_rows = [("YES→MAYBE", 2), ("MAYBE→REJECT", 1)]

    _auth_as(OWNER_EMAIL)
    _use_db([overall_row, weekly_rows, partner_rows, pair_rows])
    try:
        response = client.get("/api/v1/overrides/calibration")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()

    assert body["total_rows"] == 10
    assert body["agreements"] == 7
    assert body["disagreements"] == 3
    assert body["agreement_rate"] == 0.7
    assert body["excluded_test_account"] == "almuhammed@raed.vc"

    assert len(body["agreement_over_time"]) == 2
    assert body["agreement_over_time"][0]["total"] == 4
    assert body["agreement_over_time"][0]["agreements"] == 3
    assert body["agreement_over_time"][0]["agreement_rate"] == 0.75
    assert body["agreement_over_time"][1]["agreement_rate"] == round(4 / 6, 4)

    assert body["disagreement_pairs"] == {"YES→MAYBE": 2, "MAYBE→REJECT": 1}


def test_calibration_partner_profile_math_and_articulation_rate():
    """Per-partner articulation_rate reflects the share of rows with a
    non-empty reason/tags — the key data-quality signal from the issue."""
    overall_row = (10, 7, 3)
    weekly_rows = []
    # waleed: total=6, agreements=5, confirms=2, corrections=3, rate_downs=1, articulated=4
    # yomna:  total=4, agreements=2, confirms=0, corrections=4, rate_downs=0, articulated=1
    partner_rows = [
        ("waleed@raed.vc", 6, 5, 2, 3, 1, 4),
        ("yomna@raed.vc", 4, 2, 0, 4, 0, 1),
    ]
    pair_rows = []

    _auth_as(OWNER_EMAIL)
    _use_db([overall_row, weekly_rows, partner_rows, pair_rows])
    try:
        response = client.get("/api/v1/overrides/calibration")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    profiles = {p["acted_by_email"]: p for p in response.json()["partner_profiles"]}

    waleed = profiles["waleed@raed.vc"]
    assert waleed["total"] == 6
    assert waleed["agreement_rate"] == round(5 / 6, 4)
    assert waleed["confirm_rate"] == round(2 / 6, 4)
    assert waleed["correction_rate"] == round(3 / 6, 4)
    assert waleed["rate_down_rate"] == round(1 / 6, 4)
    assert waleed["articulation_rate"] == round(4 / 6, 4)

    yomna = profiles["yomna@raed.vc"]
    assert yomna["articulation_rate"] == round(1 / 4, 4)
    assert yomna["confirm_rate"] == 0.0


def test_calibration_weekly_buckets_use_distinct_week_starts():
    overall_row = (0, 0, 0)
    weekly_rows = [
        (_week(0), 4, 3),
        (_week(7), 6, 4),
    ]
    partner_rows = []
    pair_rows = []

    _auth_as(OWNER_EMAIL)
    _use_db([overall_row, weekly_rows, partner_rows, pair_rows])
    try:
        response = client.get("/api/v1/overrides/calibration")
    finally:
        _clear_auth()
        _clear_db()

    weeks = response.json()["agreement_over_time"]
    assert len(weeks) == 2
    assert weeks[0]["total"] != weeks[1]["total"] or weeks[0]["agreements"] != weeks[1]["agreements"]


def test_calibration_excludes_test_account_in_every_query():
    """Every one of the four aggregate queries (overall, weekly, per-partner,
    pairs) must filter out the QA test account, bound as a query param rather
    than string-interpolated."""
    overall_row = (0, 0, 0)

    _auth_as(OWNER_EMAIL)
    session = _use_db([overall_row, [], [], []])
    try:
        response = client.get("/api/v1/overrides/calibration")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert len(session.queries) == 4
    for sql, params in zip(session.queries, session.params):
        assert "IS DISTINCT FROM :test_email" in sql
        assert params == {"test_email": "almuhammed@raed.vc"}


def test_calibration_overall_rate_none_when_no_rows():
    overall_row = (0, 0, 0)

    _auth_as(OWNER_EMAIL)
    _use_db([overall_row, [], [], []])
    try:
        response = client.get("/api/v1/overrides/calibration")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert response.json()["agreement_rate"] is None
