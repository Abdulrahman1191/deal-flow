"""
Tests for bulk re-assessment (issue #203): app/services/bulk_reassess.py +
POST /leads/bulk-reassess/preview + POST /leads/bulk-reassess + GET
/leads/bulk-reassess/{batch_id}.

Built for clearing the MAYBE pile without silently burning ~12k input
tokens per lead to re-confirm a temperature=0 verdict that can't have
changed. The central correctness requirements this locks in:
  - the breaker is checked BEFORE anything else -- an open breaker queues
    and writes absolutely nothing (the 2026-10-04 incident this issue cites);
  - without `force`, only leads whose fingerprint (company name,
    description, pitch deck text, scraped website content for deck-less
    leads -- issue #192's fingerprint, defined in app/services/bulk_reassess.py
    since #192 hasn't merged) changed since their last card are queued;
  - `confirm_count` must match the live matched count or nothing is queued;
  - a lead already pending/processing is skipped, and an oversized batch is
    rejected outright.

Follows the TestClient + dependency-override + entity-routed fake session
pattern used throughout test_bulk_reassign.py / test_bulk_archive.py -- no
live Postgres needed. Every test lead carries pitch_deck_text so
bulk_reassess.classify() never actually scrapes a website over the network.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.event import LeadEvent
from app.services import bulk_reassess, llm_breaker
from app.services.auth import get_current_user
from app.services.events import EVENT_BULK_REASSESS_QUEUED
from app.tasks.assess_lead import assess_lead_task

client = TestClient(app)

OWNER_EMAIL = "reviewer@raed.vc"
PREVIEW_URL = "/api/v1/leads/bulk-reassess/preview"
EXECUTE_URL = "/api/v1/leads/bulk-reassess"


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _card(bucket="MAYBE", user_override=None, fingerprint="fp-unset", created_at=None):
    return SimpleNamespace(
        bucket=bucket,
        user_override=user_override,
        input_fingerprint=fingerprint,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _lead(n, owner_email=OWNER_EMAIL, status="assessed", card=None,
          company_name="Acme Deep Tech", description="d", pitch_deck_text="deck text"):
    return SimpleNamespace(
        id=_uid(n),
        owner_email=owner_email,
        status=status,
        company_name=company_name,
        description=description,
        pitch_deck_text=pitch_deck_text,
        website=None,
        assessment=card,
        assessment_attempts=1,
    )


def _unchanged_lead(n, **kwargs):
    """A lead whose current fingerprint exactly matches its card's stored
    one -- re-assessing it would just reproduce the same verdict."""
    company_name = kwargs.get("company_name", "Acme Deep Tech")
    description = kwargs.get("description", "d")
    pitch_deck_text = kwargs.get("pitch_deck_text", "deck text")
    fp = bulk_reassess.compute_fingerprint(company_name, description, pitch_deck_text, "")
    card = kwargs.pop("card", _card(fingerprint=fp))
    return _lead(n, card=card, **kwargs)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Routes execute() by inspecting the query, mirroring test_bulk_reassign.py:
    a two-entity select (LeadEvent, Lead) -- the progress endpoint's join --
    returns the seeded `events` rows untouched; a single-entity Lead select
    is filtered by whatever single where-clause bulk_reassess.py actually
    issues (owner_email ==, id.in_(...), or id == for the per-lead refetch
    inside execute's loop)."""

    def __init__(self, leads=None, events=None):
        self._leads = list(leads or [])
        self._events = list(events or [])
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, query):
        if len(query.column_descriptions) == 2:
            return _FakeResult(self._events)

        where = query.whereclause
        left_key = getattr(getattr(where, "left", None), "key", None)
        right_value = getattr(getattr(where, "right", None), "value", None)

        if left_key == "owner_email":
            matched = [l for l in self._leads if l.owner_email == right_value]
        elif left_key == "id":
            if isinstance(right_value, (list, tuple, set)):
                wanted = set(right_value)
                matched = [l for l in self._leads if l.id in wanted]
            else:
                matched = [l for l in self._leads if l.id == right_value]
        else:
            matched = []
        return _FakeResult(matched)

    def add(self, _obj):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _obj):
        pass


async def _fake_current_user():
    return SimpleNamespace(email=OWNER_EMAIL, is_active=True)


@pytest.fixture
def override_auth():
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _override_db(leads=None, events=None):
    session = _FakeSession(leads=leads, events=events)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


def _record_delay(monkeypatch):
    calls = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: calls.append(lead_id))
    return calls


@pytest.fixture
def no_breaker(monkeypatch):
    monkeypatch.setattr(llm_breaker, "open_reason", lambda: None)


# --- preview --------------------------------------------------------------

def test_preview_returns_matched_changed_would_skip_and_queues_nothing(override_auth, monkeypatch, no_breaker):
    unchanged = _unchanged_lead(1)
    changed = _lead(2, card=_card(fingerprint="stale-fingerprint"))
    in_flight = _unchanged_lead(3, status="processing")
    calls = _record_delay(monkeypatch)

    session = _override_db(leads=[unchanged, changed, in_flight])
    try:
        response = client.post(PREVIEW_URL, json={"bucket": "MAYBE"})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 3
    assert body["changed"] == 1
    assert body["would_skip"] == 2
    assert body["in_flight"] == 1
    assert body["would_queue"] == 1  # force=False -> would_queue == changed
    assert body["breaker_open"] is False
    assert session.commits == 0
    assert calls == []


def test_preview_would_queue_is_all_non_in_flight_when_force_true(override_auth, monkeypatch, no_breaker):
    unchanged = _unchanged_lead(1)
    in_flight = _unchanged_lead(2, status="pending")
    _record_delay(monkeypatch)

    _override_db(leads=[unchanged, in_flight])
    try:
        response = client.post(PREVIEW_URL, json={"bucket": "MAYBE", "force": True})
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 2
    assert body["in_flight"] == 1
    assert body["would_queue"] == 1  # force=True -> matched - in_flight


def test_preview_requires_lead_ids_or_a_filter(override_auth):
    _override_db(leads=[])
    try:
        response = client.post(PREVIEW_URL, json={})
    finally:
        _clear_db_override()
    assert response.status_code == 400


# --- force / unchanged skip -------------------------------------------------

def test_without_force_unchanged_lead_is_not_queued(override_auth, monkeypatch, no_breaker):
    lead = _unchanged_lead(1)
    calls = _record_delay(monkeypatch)
    session = _override_db(leads=[lead])
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "force": False, "confirm_count": 1},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 0
    assert body["skipped_unchanged"] == 1
    assert calls == []
    assert lead.status == "assessed"  # untouched
    assert session.commits == 0


def test_with_force_unchanged_lead_is_queued_anyway(override_auth, monkeypatch, no_breaker):
    lead = _unchanged_lead(1)
    calls = _record_delay(monkeypatch)
    session = _override_db(leads=[lead])
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "force": True, "confirm_count": 1},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 1
    assert body["skipped_unchanged"] == 0
    assert calls == [str(lead.id)]
    assert lead.status == "pending"
    assert lead.assessment_attempts == 0
    assert session.commits == 1


def test_changed_lead_is_queued_without_force(override_auth, monkeypatch, no_breaker):
    lead = _lead(1, card=_card(fingerprint="stale-fingerprint"))
    calls = _record_delay(monkeypatch)
    _override_db(leads=[lead])
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "force": False, "confirm_count": 1},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 1
    assert calls == [str(lead.id)]


# --- breaker ----------------------------------------------------------------

def test_open_breaker_returns_409_and_queues_nothing(override_auth, monkeypatch):
    monkeypatch.setattr(llm_breaker, "open_reason", lambda: "DeepSeek 402: Insufficient Balance")
    must_not = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resolve candidates while breaker open"))
    monkeypatch.setattr(bulk_reassess, "resolve_candidates", must_not)
    calls = _record_delay(monkeypatch)

    session = _override_db(leads=[_unchanged_lead(1)])
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "confirm_count": 1},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "402" in response.json()["detail"]
    assert calls == []
    assert session.commits == 0


# --- confirm_count -----------------------------------------------------------

def test_confirm_count_mismatch_returns_409_and_queues_nothing(override_auth, monkeypatch, no_breaker):
    leads = [_unchanged_lead(1), _lead(2, card=_card(fingerprint="stale"))]
    calls = _record_delay(monkeypatch)

    session = _override_db(leads=leads)
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "confirm_count": 1},  # actual count is 2
        )
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "confirm_count" in response.json()["detail"]
    assert calls == []
    assert session.commits == 0


# --- in-flight skip + batch cap ---------------------------------------------

def test_in_flight_lead_is_skipped_at_execute_time(override_auth, monkeypatch, no_breaker):
    in_flight = _unchanged_lead(1, status="processing")
    calls = _record_delay(monkeypatch)
    _override_db(leads=[in_flight])
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "force": True, "confirm_count": 1},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 0
    assert body["skipped_in_flight"] == 1
    assert calls == []
    assert in_flight.status == "processing"  # untouched


def test_batch_over_cap_is_rejected_with_a_clear_message(override_auth, monkeypatch, no_breaker):
    monkeypatch.setattr(settings, "bulk_reassess_batch_cap", 1)
    leads = [_unchanged_lead(1), _lead(2, card=_card(fingerprint="stale"))]
    calls = _record_delay(monkeypatch)

    session = _override_db(leads=leads)
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "confirm_count": 2},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 409
    assert "exceeds the cap" in response.json()["detail"]
    assert calls == []
    assert session.commits == 0


# --- per-lead isolation ------------------------------------------------------

def test_one_failing_lead_does_not_abort_the_batch(override_auth, monkeypatch, no_breaker):
    good = _lead(1, card=_card(fingerprint="stale"))
    bad = _lead(2, card=_card(fingerprint="stale"))
    calls = _record_delay(monkeypatch)

    class _RaisingLogEvent:
        async def __call__(self, db, lead_id, event_type, payload=None):
            if lead_id == bad.id:
                raise RuntimeError("db exploded")

    monkeypatch.setattr("app.routers.leads.log_event", _RaisingLogEvent())

    session = _override_db(leads=[good, bad])
    try:
        response = client.post(
            EXECUTE_URL, json={"bucket": "MAYBE", "confirm_count": 2},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["lead_id"] == str(bad.id)
    assert session.rollbacks == 1
    assert calls == [str(good.id)]


# --- ownership / impersonation ----------------------------------------------

def test_explicit_lead_id_owned_by_someone_else_is_forbidden(override_auth, no_breaker):
    someone_elses_lead = _unchanged_lead(1, owner_email="not-the-caller@raed.vc")
    _override_db(leads=[someone_elses_lead])
    try:
        response = client.post(
            PREVIEW_URL, json={"lead_ids": [str(someone_elses_lead.id)]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 403


def test_admin_under_view_as_is_blocked(monkeypatch, no_breaker):
    async def _fake_admin_user():
        return SimpleNamespace(email=settings.owner_email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_admin_user
    _override_db(leads=[])
    try:
        response = client.post(
            f"{EXECUTE_URL}?view_as=someone-else@raed.vc",
            json={"bucket": "MAYBE", "confirm_count": 0},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        _clear_db_override()

    assert response.status_code == 403
    assert "Read-only while viewing another user's board" in response.json()["detail"]


# --- progress ----------------------------------------------------------------

def test_summarize_progress_counts_bucket_changes_and_in_flight_state():
    batch_id = uuid.uuid4()
    moved_lead = _lead(1, status="assessed", card=_card(bucket="YES"))
    same_lead = _lead(2, status="assessed", card=_card(bucket="MAYBE"))
    pending_lead = _lead(3, status="pending", card=_card(bucket="MAYBE"))
    failed_lead = _lead(4, status="failed", card=_card(bucket="MAYBE"))

    rows = [
        (LeadEvent(lead_id=moved_lead.id, event_type=EVENT_BULK_REASSESS_QUEUED,
                    payload={"batch_id": str(batch_id), "bucket_before": "MAYBE"}), moved_lead),
        (LeadEvent(lead_id=same_lead.id, event_type=EVENT_BULK_REASSESS_QUEUED,
                    payload={"batch_id": str(batch_id), "bucket_before": "MAYBE"}), same_lead),
        (LeadEvent(lead_id=pending_lead.id, event_type=EVENT_BULK_REASSESS_QUEUED,
                    payload={"batch_id": str(batch_id), "bucket_before": "MAYBE"}), pending_lead),
        (LeadEvent(lead_id=failed_lead.id, event_type=EVENT_BULK_REASSESS_QUEUED,
                    payload={"batch_id": str(batch_id), "bucket_before": "MAYBE"}), failed_lead),
    ]

    progress = bulk_reassess.summarize_progress(batch_id, rows)

    assert progress["queued"] == 4
    assert progress["assessed"] == 2
    assert progress["pending"] == 1
    assert progress["failed"] == 1
    assert progress["bucket_changed"] == 1   # moved_lead: MAYBE -> YES
    assert progress["bucket_unchanged"] == 1  # same_lead: MAYBE -> MAYBE


def test_progress_endpoint_returns_404_when_batch_has_no_events(override_auth):
    _override_db(leads=[], events=[])
    try:
        response = client.get(f"{EXECUTE_URL}/{uuid.uuid4()}")
    finally:
        _clear_db_override()

    assert response.status_code == 404


def test_progress_endpoint_returns_summarized_counts(override_auth):
    batch_id = uuid.uuid4()
    moved_lead = _lead(1, status="assessed", card=_card(bucket="YES"))
    event = LeadEvent(
        lead_id=moved_lead.id, event_type=EVENT_BULK_REASSESS_QUEUED,
        payload={"batch_id": str(batch_id), "bucket_before": "MAYBE"},
    )

    _override_db(leads=[], events=[(event, moved_lead)])
    try:
        response = client.get(f"{EXECUTE_URL}/{batch_id}")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 1
    assert body["assessed"] == 1
    assert body["bucket_changed"] == 1
    assert body["bucket_unchanged"] == 0
