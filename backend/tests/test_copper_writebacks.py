"""
Tests for Copper write-backs triggered by accept-meeting / reject / archive
actions (issue #42): does the platform actually tell Copper about a decision?

Copper is always mocked here — either by stubbing the copper_writer function
the router calls, or by stubbing copper_writer._enqueue / httpx so no network
call happens. Live verification against real Copper lives in
scripts/test_copper_sync.py (`--check` for connectivity, full run for an
end-to-end disposable-lead round trip) and is never run in CI.

Router-level tests mirror the TestClient + dependency-override pattern used in
test_assessment_rating_gate.py / test_archive_no_reply_gate.py — no live
Postgres needed.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import copper_writer, email_sender
from app.services.auth import get_current_user
from app.tasks import drain_outbox

client = TestClient(app)


# ---------------------------------------------------------------------------
# Shared fakes (router-level tests)
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


class _FakeSession:
    """Returns queued results for each execute() call in order, mirroring
    the pattern in test_assessment_rating_gate.py / test_archive_no_reply_gate.py."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _fake_card(rated: bool, bucket: str = "YES", draft_type: str = "meeting_request"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        user_override=None,
        user_rating="up" if rated else None,
        confidence_score=80,
        summary="promising deep-tech team",
        scoring_breakdown={},
        research_data={},
        draft_type=draft_type,
        draft_subject="Let's talk",
        draft_body="Hi there",
        approved_at=None,
        sent_at=None,
    )


def _fake_lead(copper_id=None, copper_opportunity_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        copper_person_id=None,
        copper_company_id=None,
        company_name="Acme Deep Tech",
        founder_names=["Jane Founder"],
        raw_copper_data={"recipient_email": "founder@acme.test", "tags": ["existing-tag"]},
        pitch_deck_text=None,
        status="pending",
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
    async def _fake_get_db():
        yield _FakeSession(results)

    app.dependency_overrides[get_db] = _fake_get_db


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


def _configure_send(monkeypatch):
    monkeypatch.setattr(email_sender, "is_configured", lambda: True)
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# 1. YES meeting_request send -> convert_lead_to_opportunity
# ---------------------------------------------------------------------------

def test_send_meeting_request_yes_converts_lead_to_opportunity(override_auth, monkeypatch):
    _configure_send(monkeypatch)
    # mark-approved also fires on first send; stub it out so it doesn't try
    # to hit a real outbox DB.
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    calls = []

    def fake_convert(copper_id, company_name, founder_name):
        calls.append((copper_id, company_name, founder_name))
        return {"person_id": "p1", "company_id": "c1", "opportunity_id": "o1"}

    monkeypatch.setattr(copper_writer, "convert_lead_to_opportunity", fake_convert)

    card = _fake_card(rated=True, bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(copper_id="12345")
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["converted"] is True
    assert calls == [("12345", "Acme Deep Tech", "Jane Founder")]
    assert lead.copper_opportunity_id == "o1"


def test_send_meeting_request_no_copper_id_skips_write_and_does_not_error(override_auth, monkeypatch):
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    calls = []
    monkeypatch.setattr(
        copper_writer, "convert_lead_to_opportunity", lambda *a, **k: calls.append(a)
    )

    card = _fake_card(rated=True, bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(copper_id=None)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == []
    assert response.json()["converted"] is False


# ---------------------------------------------------------------------------
# 2. rejection send -> archive_in_copper -> outbox row targets PUT /leads/{id}
#    with the Unqualified status_id
# ---------------------------------------------------------------------------

def test_send_rejection_calls_archive_in_copper(override_auth, monkeypatch):
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags: calls.append((copper_id, existing_tags)),
    )

    card = _fake_card(rated=True, bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(copper_id="98765")
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [("98765", ["existing-tag"])]


def test_archive_in_copper_enqueues_unqualified_status_put(monkeypatch):
    """Unit test of copper_writer.archive_in_copper itself: confirms the
    outbox row it enqueues targets PUT /leads/{copper_id} with the
    Unqualified status_id — independent of which router calls it."""
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 999)

    enqueued = []
    monkeypatch.setattr(
        copper_writer,
        "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(
            {"copper_id": copper_id, "endpoint": endpoint, "body": body, "method": method}
        ),
    )

    copper_writer.archive_in_copper("55555", ["some-tag"])

    assert len(enqueued) == 1
    call = enqueued[0]
    assert call["copper_id"] == "55555"
    assert call["endpoint"] == "/leads/55555"
    assert call["method"] == "PUT"
    assert call["body"]["status_id"] == 999
    assert "raed:archived" in call["body"]["tags"]


def test_send_rejection_no_copper_id_skips_write_and_does_not_error(override_auth, monkeypatch):
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    calls = []
    monkeypatch.setattr(
        copper_writer, "archive_in_copper", lambda *a, **k: calls.append(a)
    )

    card = _fake_card(rated=True, bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(copper_id=None)
    _override_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == []


# ---------------------------------------------------------------------------
# 3. POST /leads/{id}/archive-no-reply -> archive_in_copper
# ---------------------------------------------------------------------------

def test_archive_no_reply_enqueues_reject_copper_write(override_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags: calls.append((copper_id, existing_tags)),
    )

    lead = _fake_lead(copper_id="11122")
    card = _fake_card(rated=True, bucket="MAYBE", draft_type=None)
    _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [("11122", ["existing-tag"])]


def test_archive_no_reply_no_copper_id_skips_write(override_auth, monkeypatch):
    calls = []
    monkeypatch.setattr(copper_writer, "archive_in_copper", lambda *a, **k: calls.append(a))

    lead = _fake_lead(copper_id=None)
    card = _fake_card(rated=True, bucket="MAYBE", draft_type=None)
    _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == []


def test_archive_no_reply_already_converted_skips_copper_write(override_auth, monkeypatch):
    """A lead that already has a Copper opportunity (converted) shouldn't get
    an archive write — there's no open Lead left in Copper to archive."""
    calls = []
    monkeypatch.setattr(copper_writer, "archive_in_copper", lambda *a, **k: calls.append(a))

    lead = _fake_lead(copper_id="11122", copper_opportunity_id="o-999")
    card = _fake_card(rated=True, bucket="MAYBE", draft_type=None)
    _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == []


# ---------------------------------------------------------------------------
# 4. execute_copper_request itself: mocked httpx.Client, correct call shape
# ---------------------------------------------------------------------------

class _FakeHttpResponse:
    def __init__(self, json_data=None, raise_exc=None):
        self._json_data = json_data or {}
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        return self._json_data


class _FakeHttpClient:
    def __init__(self, calls, response):
        self._calls = calls
        self._response = response

    def __call__(self, timeout=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def request(self, method, url, headers=None, json=None):
        self._calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return self._response


def test_execute_copper_request_success_hits_correct_endpoint(monkeypatch):
    calls = []
    fake_client = _FakeHttpClient(calls, _FakeHttpResponse(json_data={"id": "42", "status_id": 999}))
    monkeypatch.setattr(copper_writer.httpx, "Client", fake_client)

    result = copper_writer.execute_copper_request("/leads/42", "PUT", {"status_id": 999})

    assert result == {"id": "42", "status_id": 999}
    assert calls == [{
        "method": "PUT",
        "url": f"{copper_writer.COPPER_BASE}/leads/42",
        "headers": calls[0]["headers"],
        "json": {"status_id": 999},
    }]


def test_execute_copper_request_raises_on_http_error_no_swallowing(monkeypatch):
    calls = []
    fake_client = _FakeHttpClient(calls, _FakeHttpResponse(raise_exc=RuntimeError("copper 500")))
    monkeypatch.setattr(copper_writer.httpx, "Client", fake_client)

    with pytest.raises(RuntimeError, match="copper 500"):
        copper_writer.execute_copper_request("/leads/42", "PUT", {"status_id": 999})

    assert len(calls) == 1  # the call was made -- it just failed downstream


# ---------------------------------------------------------------------------
# 5. Outbox drain path: drain_outbox._drain() -> execute_copper_request
# ---------------------------------------------------------------------------

def _outbox_row(**overrides):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        copper_id="1",
        endpoint="/leads/1",
        method="PUT",
        body_json={"status_id": 999},
        status="pending",
        attempts=0,
        next_attempt_at=None,
        last_error=None,
        updated_at=None,
        created_at=None,
    )
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


class _FakeOutboxSession:
    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _query):
        rows = self._rows
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    async def commit(self):
        self.committed = True


def _run_drain(monkeypatch, rows, execute_fn):
    session = _FakeOutboxSession(rows)
    monkeypatch.setattr(drain_outbox, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(drain_outbox, "execute_copper_request", execute_fn)
    result = asyncio.run(drain_outbox._drain())
    return result, session


def test_drain_outbox_success_marks_row_done_and_calls_execute_copper_request(monkeypatch):
    row = _outbox_row(endpoint="/leads/42", method="PUT", body_json={"status_id": 999})
    calls = []

    def fake_execute(endpoint, method, body):
        calls.append((endpoint, method, body))
        return {"id": 42}

    result, session = _run_drain(monkeypatch, [row], fake_execute)

    assert calls == [("/leads/42", "PUT", {"status_id": 999})]
    assert row.status == "done"
    assert result == {"done": 1, "retried": 0, "failed": 0}
    assert session.committed is True


def test_drain_outbox_failure_retries_row_with_backoff(monkeypatch):
    row = _outbox_row(attempts=0)

    def fake_execute(endpoint, method, body):
        raise RuntimeError("copper 500")

    result, session = _run_drain(monkeypatch, [row], fake_execute)

    assert row.status == "pending"  # not yet exhausted -> stays pending for retry
    assert row.attempts == 1
    assert row.last_error and "copper 500" in row.last_error
    assert result == {"done": 0, "retried": 1, "failed": 0}


def test_drain_outbox_failure_marked_failed_after_max_attempts(monkeypatch):
    row = _outbox_row(attempts=copper_writer.MAX_ATTEMPTS - 1)

    def fake_execute(endpoint, method, body):
        raise RuntimeError("copper still down")

    result, session = _run_drain(monkeypatch, [row], fake_execute)

    assert row.attempts == copper_writer.MAX_ATTEMPTS
    assert row.status == "failed"
    assert result == {"done": 0, "retried": 0, "failed": 1}


def test_drain_outbox_no_network_call_made_directly(monkeypatch):
    """Guard against a regression that bypasses execute_copper_request and
    hits httpx directly — drain must always go through the mockable seam."""
    import httpx

    def _boom(*a, **k):
        raise AssertionError("drain_outbox must not call httpx directly")

    monkeypatch.setattr(httpx, "Client", _boom)

    row = _outbox_row()
    result, _session = _run_drain(monkeypatch, [row], lambda *a, **k: {"ok": True})

    assert result == {"done": 1, "retried": 0, "failed": 0}
