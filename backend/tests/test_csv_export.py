"""
Tests for the YES-bucket leads CSV export (GET /api/v1/leads/export).

The CSV rendering and bucket-resolution logic is factored into
app.services.csv_export so it's testable without a live database — the
router itself just queries leads and hands rows to build_leads_csv.

The endpoint itself (routing, query-param filtering, response headers) is
exercised below with fastapi.testclient.TestClient against app.database.get_db
and app.services.auth.get_current_user stubs — mirrors the pattern in
test_health.py, no live Postgres needed.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.services.csv_export import LEADS_CSV_HEADERS, build_leads_csv, effective_bucket


def test_empty_export_returns_headers_only():
    csv_text = build_leads_csv([])
    lines = csv_text.strip("\r\n").split("\r\n")

    assert len(lines) == 1
    assert lines[0] == ",".join(LEADS_CSV_HEADERS)


def test_export_includes_matching_rows():
    rows = [
        {
            "company_name": "Acme Deep Tech",
            "bucket": "YES",
            "confidence_score": 88,
            "created_date": "2026-07-01T00:00:00+00:00",
        }
    ]
    csv_text = build_leads_csv(rows)
    lines = csv_text.strip("\r\n").split("\r\n")

    assert lines[0] == ",".join(LEADS_CSV_HEADERS)
    assert lines[1] == "Acme Deep Tech,YES,88,2026-07-01T00:00:00+00:00"


def test_export_quotes_company_names_with_commas():
    rows = [
        {
            "company_name": "Acme, Inc.",
            "bucket": "YES",
            "confidence_score": 90,
            "created_date": "2026-07-01T00:00:00+00:00",
        }
    ]
    csv_text = build_leads_csv(rows)

    assert '"Acme, Inc."' in csv_text


def test_effective_bucket_prefers_user_override():
    assessment = SimpleNamespace(bucket="MAYBE", user_override="YES")
    assert effective_bucket(assessment) == "YES"


def test_effective_bucket_falls_back_to_ai_bucket():
    assessment = SimpleNamespace(bucket="YES", user_override=None)
    assert effective_bucket(assessment) == "YES"


def test_effective_bucket_none_when_no_assessment():
    assert effective_bucket(None) is None


class _FakeResult:
    """Stands in for the SQLAlchemy Result returned by AsyncSession.execute —
    the router only calls .scalars().all() on it."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal stand-in for AsyncSession — ignores the query and always
    returns the leads it was constructed with (bucket filtering happens in
    the router, in Python, which is what these tests exercise)."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _FakeResult(self._rows)


def _fake_lead(company_name, bucket):
    assessment = SimpleNamespace(bucket=bucket, user_override=None, confidence_score=77)
    return SimpleNamespace(
        company_name=company_name,
        owner_email="founder@raed.vc",
        assessment=assessment,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


async def _fake_current_user():
    return SimpleNamespace(email="founder@raed.vc", is_active=True)


@pytest.fixture
def override_auth():
    """Stubs get_current_user for the duration of a test; leaves get_db to
    the individual test so each can control the fake leads returned."""
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_export_endpoint_filters_by_bucket(override_auth):
    leads = [_fake_lead("Acme Deep Tech", "YES"), _fake_lead("Not A Fit Co", "REJECT")]

    async def _fake_get_db():
        yield _FakeSession(leads)

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = TestClient(app).get("/api/v1/leads/export?bucket=YES")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip("\r\n").split("\r\n")
    assert lines[0] == ",".join(LEADS_CSV_HEADERS)
    assert len(lines) == 2
    assert "Acme Deep Tech" in lines[1]
    assert "Not A Fit Co" not in response.text


def test_export_endpoint_headers_only_when_no_matches(override_auth):
    async def _fake_get_db():
        yield _FakeSession([])

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = TestClient(app).get("/api/v1/leads/export?bucket=YES")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.text.strip("\r\n") == ",".join(LEADS_CSV_HEADERS)


def test_export_route_not_shadowed_by_lead_id_route(override_auth):
    """/leads/export must dispatch to export_leads, not get_lead(lead_id="export")."""
    leads = [_fake_lead("Acme Deep Tech", "YES")]

    async def _fake_get_db():
        yield _FakeSession(leads)

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = TestClient(app).get("/api/v1/leads/export")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Acme Deep Tech" in response.text
