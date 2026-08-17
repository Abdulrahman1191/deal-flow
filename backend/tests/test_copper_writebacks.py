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
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.routers import assessments
from app.services import claude_agent, copper_writer, email_sender
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
        red_flags=[],
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
    return SimpleNamespace(email="reviewer@raed.vc", is_active=True, copper_user_id=None)


@pytest.fixture
def override_auth():
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def override_auth_with_copper_id():
    """Same as override_auth, but the acting user already has a cached
    copper_user_id -- used to assert assignee routing on convert."""
    async def _fake_user_with_copper_id():
        return SimpleNamespace(email="reviewer@raed.vc", is_active=True, copper_user_id=777)

    app.dependency_overrides[get_current_user] = _fake_user_with_copper_id
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
    # override_auth's user has no cached copper_user_id, so _finalize_sent
    # falls through to resolve_copper_id -> lookup_user_id, which would
    # otherwise open a real httpx.Client against Copper's API. Stub the
    # resolution seam so the fallback-to-unassigned path is exercised with
    # no network call.
    async def _fake_resolve_copper_id(_db, _user):
        return None

    monkeypatch.setattr(assessments, "resolve_copper_id", _fake_resolve_copper_id)

    calls = []

    def fake_convert(copper_id, company_name, founder_name, assignee_id=None):
        calls.append((copper_id, company_name, founder_name, assignee_id))
        return {"person_id": "p1", "company_id": "c1", "opportunity_id": "o1"}

    monkeypatch.setattr(copper_writer, "convert_lead_to_opportunity", fake_convert)

    card = _fake_card(rated=True, bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(copper_id="12345")
    _override_db([(card, lead), None])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["converted"] is True
    # Acting user (override_auth) has no copper_user_id -- falls back to
    # unassigned rather than blocking the conversion.
    assert calls == [("12345", "Acme Deep Tech", "Jane Founder", None)]
    assert lead.copper_opportunity_id == "o1"


def test_send_meeting_request_assigns_opportunity_to_acting_user(override_auth_with_copper_id, monkeypatch):
    """When the acting user has a resolvable copper_user_id, it must be
    passed through to convert_lead_to_opportunity as assignee_id so the new
    opportunity lands on their Kanban (issue #64)."""
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    calls = []

    def fake_convert(copper_id, company_name, founder_name, assignee_id=None):
        calls.append((copper_id, company_name, founder_name, assignee_id))
        return {"person_id": "p1", "company_id": "c1", "opportunity_id": "o1"}

    monkeypatch.setattr(copper_writer, "convert_lead_to_opportunity", fake_convert)

    card = _fake_card(rated=True, bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(copper_id="12345")
    _override_db([(card, lead), None])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["converted"] is True
    assert calls == [("12345", "Acme Deep Tech", "Jane Founder", 777)]


def test_send_meeting_request_no_copper_id_skips_write_and_does_not_error(override_auth, monkeypatch):
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    calls = []
    monkeypatch.setattr(
        copper_writer, "convert_lead_to_opportunity", lambda *a, **k: calls.append(a)
    )

    card = _fake_card(rated=True, bucket="YES", draft_type="meeting_request")
    lead = _fake_lead(copper_id=None)
    _override_db([(card, lead), None])
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
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {"reason_option_ids": [367302], "detail_text": "Lack of traction."},
    )

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    card = _fake_card(rated=True, bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(copper_id="98765")
    _override_db([(card, lead), None])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [(
        "98765",
        ["existing-tag"],
        {"reason_option_ids": [367302], "detail_text": "Lack of traction."},
    )]


def test_send_rejection_still_archives_when_unqual_ai_call_fails(override_auth, monkeypatch):
    """Best-effort: an AI failure must not block the archive write itself —
    only the reason/detail are omitted."""
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    def _boom(**kwargs):
        raise RuntimeError("deepseek down")

    monkeypatch.setattr(claude_agent, "generate_unqualification_reason", _boom)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    card = _fake_card(rated=True, bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(copper_id="98765")
    _override_db([(card, lead), None])
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [("98765", ["existing-tag"], {"reason_option_ids": None, "detail_text": None})]


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
    assert "custom_fields" not in call["body"]


def test_archive_in_copper_omits_unqual_fields_when_ids_not_configured(monkeypatch):
    """Field ids default to 0 — reason/detail must never be sent unless both
    are explicitly configured, even when the caller passes values."""
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 999)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_reason_id", 0)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_detail_id", 0)

    enqueued = []
    monkeypatch.setattr(
        copper_writer,
        "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(body),
    )

    copper_writer.archive_in_copper(
        "55555", ["some-tag"], reason_option_ids=[367311], detail_text="Other."
    )

    assert "custom_fields" not in enqueued[0]


def test_reject_in_copper_includes_unqual_custom_fields_when_configured(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 999)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_reason_id", 244358)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_detail_id", 244359)

    enqueued = []
    monkeypatch.setattr(
        copper_writer,
        "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(body),
    )

    copper_writer.reject_in_copper(
        "55555", ["some-tag"], reason_option_ids=[367300], detail_text="Out of our stage."
    )

    custom_fields = enqueued[0]["custom_fields"]
    assert custom_fields == [
        {"custom_field_definition_id": 244358, "value": [367300]},
        {"custom_field_definition_id": 244359, "value": "Out of our stage."},
    ]


def test_send_rejection_no_copper_id_skips_write_and_does_not_error(override_auth, monkeypatch):
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    calls = []
    monkeypatch.setattr(
        copper_writer, "archive_in_copper", lambda *a, **k: calls.append(a)
    )

    card = _fake_card(rated=True, bucket="REJECT", draft_type="rejection")
    lead = _fake_lead(copper_id=None)
    _override_db([(card, lead), None])
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
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {"reason_option_ids": [367311], "detail_text": "Not a fit for now."},
    )

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    lead = _fake_lead(copper_id="11122")
    card = _fake_card(rated=True, bucket="MAYBE", draft_type=None)
    _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [(
        "11122",
        ["existing-tag"],
        {"reason_option_ids": [367311], "detail_text": "Not a fit for now."},
    )]


def test_archive_no_reply_enqueues_unqual_custom_fields_when_configured(override_auth, monkeypatch):
    """Acceptance test (issue #63): archive-no-reply on a lead with an
    assessment enqueues a Copper PUT whose body includes `custom_fields` for
    both configured unqual-reason ids, with reason option ids drawn only
    from the fixed allowed set."""
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 999)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_reason_id", 244358)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_detail_id", 244359)
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {
            "reason_option_ids": [367302, 367305],
            "detail_text": "Traction and market size don't fit our thesis right now.",
        },
    )

    enqueued = []
    monkeypatch.setattr(
        copper_writer,
        "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(body),
    )

    lead = _fake_lead(copper_id="55555")
    card = _fake_card(rated=True, bucket="REJECT", draft_type="rejection")
    _override_db([lead, card])
    try:
        response = client.post(f"/api/v1/leads/{lead.id}/archive-no-reply")
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert len(enqueued) == 1
    custom_fields = enqueued[0]["custom_fields"]
    reason_field = next(f for f in custom_fields if f["custom_field_definition_id"] == 244358)
    detail_field = next(f for f in custom_fields if f["custom_field_definition_id"] == 244359)

    allowed_ids = set(claude_agent.UNQUAL_REASON_OPTIONS.values())
    assert reason_field["value"] == [367302, 367305]
    assert set(reason_field["value"]) <= allowed_ids
    assert detail_field["value"] == "Traction and market size don't fit our thesis right now."


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
# 4b. convert_lead_to_opportunity itself: assignee_id routing (issue #64)
# ---------------------------------------------------------------------------

class _FakeConvertClient:
    """Mocks httpx.Client for convert_lead_to_opportunity, which calls
    client.post (convert) and, when an assignee_id is given, client.put
    (the follow-up assignee write) -- unlike _FakeHttpClient above, which only
    covers the generic client.request() seam used by execute_copper_request."""

    def __init__(self, calls, convert_response, put_response=None):
        self._calls = calls
        self._convert_response = convert_response
        self._put_response = put_response or _FakeHttpResponse(json_data={})

    def __call__(self, timeout=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, json=None):
        self._calls.append({"method": "POST", "url": url, "json": json})
        return self._convert_response

    def put(self, url, headers=None, json=None):
        self._calls.append({"method": "PUT", "url": url, "json": json})
        return self._put_response


def _configure_pipeline(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_pipeline_id", 111)
    monkeypatch.setattr(copper_writer.settings, "copper_pipeline_stage_id", 222)


def test_convert_with_assignee_id_sets_it_inline_and_follows_up_with_put(monkeypatch):
    _configure_pipeline(monkeypatch)
    calls = []
    convert_response = _FakeHttpResponse(
        json_data={"person": {"id": 1}, "company": {"id": 2}, "opportunity": {"id": 3}}
    )
    fake_client = _FakeConvertClient(calls, convert_response)
    monkeypatch.setattr(copper_writer.httpx, "Client", fake_client)

    result = copper_writer.convert_lead_to_opportunity(
        "12345", "Acme Deep Tech", "Jane Founder", assignee_id=777
    )

    assert result == {"person_id": "1", "company_id": "2", "opportunity_id": "3"}
    assert len(calls) == 2
    convert_call, put_call = calls
    assert convert_call["method"] == "POST"
    assert convert_call["json"]["details"]["opportunity"]["assignee_id"] == 777
    assert put_call["method"] == "PUT"
    assert put_call["url"] == f"{copper_writer.COPPER_BASE}/opportunities/3"
    assert put_call["json"] == {"assignee_id": 777}


def test_convert_without_assignee_id_skips_follow_up_put(monkeypatch):
    """A user with no resolvable copper_user_id (assignee_id=None) still
    converts without error and without the extra PUT -- matches prior
    unassigned behavior."""
    _configure_pipeline(monkeypatch)
    calls = []
    convert_response = _FakeHttpResponse(
        json_data={"person": {"id": 1}, "company": {"id": 2}, "opportunity": {"id": 3}}
    )
    fake_client = _FakeConvertClient(calls, convert_response)
    monkeypatch.setattr(copper_writer.httpx, "Client", fake_client)

    result = copper_writer.convert_lead_to_opportunity("12345", "Acme Deep Tech", "Jane Founder")

    assert result == {"person_id": "1", "company_id": "2", "opportunity_id": "3"}
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert "assignee_id" not in calls[0]["json"]["details"]["opportunity"]


def test_convert_assignee_put_failure_does_not_lose_the_created_opportunity(monkeypatch):
    """If the follow-up assignee PUT fails, the opportunity was still created
    -- return the ids instead of surfacing None, so the lead row still gets
    linked (falls back to unassigned in Copper, matching the acceptance
    criteria's 'do not block the conversion')."""
    _configure_pipeline(monkeypatch)
    calls = []
    convert_response = _FakeHttpResponse(
        json_data={"person": {"id": 1}, "company": {"id": 2}, "opportunity": {"id": 3}}
    )
    put_response = _FakeHttpResponse(raise_exc=RuntimeError("copper 500"))
    fake_client = _FakeConvertClient(calls, convert_response, put_response)
    monkeypatch.setattr(copper_writer.httpx, "Client", fake_client)

    result = copper_writer.convert_lead_to_opportunity(
        "12345", "Acme Deep Tech", "Jane Founder", assignee_id=777
    )

    assert result == {"person_id": "1", "company_id": "2", "opportunity_id": "3"}


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


# ---------------------------------------------------------------------------
# 6. Config-unset skips (issue #65): archive/reject must leave a visible
#    marker instead of a bare print()+return.
# ---------------------------------------------------------------------------

def test_archive_in_copper_records_skip_marker_when_status_id_unset(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 0)

    recorded = []
    monkeypatch.setattr(
        copper_writer,
        "_record_skipped_write",
        lambda copper_id, endpoint, method, reason: recorded.append(
            {"copper_id": copper_id, "endpoint": endpoint, "method": method, "reason": reason}
        ),
    )
    enqueued = []
    monkeypatch.setattr(copper_writer, "_enqueue", lambda *a, **k: enqueued.append(a))

    copper_writer.archive_in_copper("55555", ["some-tag"])

    assert enqueued == []  # no real write attempted
    assert len(recorded) == 1
    assert recorded[0]["copper_id"] == "55555"
    assert recorded[0]["endpoint"] == "/leads/55555"
    assert "copper_unqualified_status_id unset" in recorded[0]["reason"]


def test_reject_in_copper_records_skip_marker_when_status_id_unset(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 0)

    recorded = []
    monkeypatch.setattr(
        copper_writer,
        "_record_skipped_write",
        lambda copper_id, endpoint, method, reason: recorded.append(
            {"copper_id": copper_id, "endpoint": endpoint, "method": method, "reason": reason}
        ),
    )

    copper_writer.reject_in_copper("55555", ["some-tag"])

    assert len(recorded) == 1
    assert "copper_unqualified_status_id unset" in recorded[0]["reason"]


def test_record_skipped_write_inserts_failed_outbox_row_and_logs_warning(monkeypatch, caplog):
    """Unit test of _record_skipped_write itself: confirms it inserts a row
    already marked status="failed" with a clear last_error, and emits a
    WARNING-level log with a distinct tag -- so a missing config id shows up
    in outbox-health / logs instead of vanishing as a stdout print()."""
    inserted = []

    class _FakeSyncSession:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add(self, row):
            inserted.append(row)

        def commit(self):
            pass

    monkeypatch.setattr(copper_writer, "_sync_engine", lambda: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr("sqlalchemy.orm.Session", _FakeSyncSession)

    with caplog.at_level("WARNING"):
        copper_writer._record_skipped_write(
            "999", "/leads/999", "PUT", "skipped archive_in_copper: copper_unqualified_status_id unset"
        )

    assert len(inserted) == 1
    row = inserted[0]
    assert row.status == "failed"
    assert row.last_error == "skipped archive_in_copper: copper_unqualified_status_id unset"
    assert any("config_unset" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 7. GET /leads/outbox-health (issue #65): admin-only counts + recent failures
# ---------------------------------------------------------------------------

class _FakeOutboxHealthSession:
    """Returns the group-by counts on the first execute() and the recent
    failed rows on the second, mirroring the two queries the endpoint runs."""

    def __init__(self, count_rows, failed_rows):
        self._count_rows = count_rows
        self._failed_rows = failed_rows
        self._call = 0

    async def execute(self, _query):
        self._call += 1
        if self._call == 1:
            return SimpleNamespace(all=lambda: self._count_rows)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self._failed_rows))


def _fake_admin_user():
    return SimpleNamespace(email=settings.owner_email, is_active=True)


def _fake_non_admin_user():
    return SimpleNamespace(email="not-an-admin@raed.vc", is_active=True)


def _failed_outbox_row(**overrides):
    row = SimpleNamespace(
        endpoint="/leads/55555",
        copper_id="55555",
        method="PUT",
        attempts=5,
        last_error="skipped archive_in_copper: copper_unqualified_status_id unset",
        created_at=datetime.now(timezone.utc),
    )
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def test_outbox_health_returns_counts_and_recent_failures_for_admin():
    async def _fake_get_db():
        yield _FakeOutboxHealthSession(
            count_rows=[("pending", 3), ("done", 10), ("failed", 2)],
            failed_rows=[_failed_outbox_row()],
        )

    app.dependency_overrides[get_current_user] = _fake_admin_user
    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = client.get("/api/v1/leads/outbox-health")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"pending": 3, "done": 10, "failed": 2}
    assert len(body["recent_failed"]) == 1
    failure = body["recent_failed"][0]
    assert failure["copper_id"] == "55555"
    assert failure["endpoint"] == "/leads/55555"
    assert failure["attempts"] == 5
    assert "copper_unqualified_status_id unset" in failure["last_error"]


# ---------------------------------------------------------------------------
# 8. _sync_engine SSL param normalization (issue #120): the async DATABASE_URL
#    uses asyncpg's `ssl=require`, which psycopg2 rejects outright
#    (`invalid connection option "ssl"`) if passed straight through, silently
#    failing every _enqueue call. Must translate to psycopg2's `sslmode`.
# ---------------------------------------------------------------------------

def test_psycopg2_url_translates_asyncpg_ssl_require_to_sslmode_connect_arg():
    url, connect_args = copper_writer._psycopg2_url_and_connect_args(
        "postgresql+asyncpg://u:p@h/db?ssl=require"
    )
    assert connect_args == {"sslmode": "require"}
    assert "ssl" not in dict(url.query)
    assert "sslmode" not in dict(url.query)  # lives in connect_args, not the URL
    assert "ssl=" not in str(url)
    assert url.drivername == "postgresql"


def test_psycopg2_url_translates_asyncpg_ssl_true_to_sslmode_require():
    url, connect_args = copper_writer._psycopg2_url_and_connect_args(
        "postgresql+asyncpg://u:p@h/db?ssl=true"
    )
    assert connect_args == {"sslmode": "require"}
    assert "ssl" not in dict(url.query)


def test_psycopg2_url_no_ssl_param_passes_through_unchanged():
    url, connect_args = copper_writer._psycopg2_url_and_connect_args(
        "postgresql+asyncpg://u:p@h/db"
    )
    assert connect_args == {}
    assert dict(url.query) == {}
    assert url.drivername == "postgresql"
    assert url.password == "p"


def test_psycopg2_url_existing_sslmode_passes_through_unchanged():
    """A URL already written with psycopg2-style sslmode (e.g. hand-configured
    for local dev) must not be touched or duplicated."""
    url, connect_args = copper_writer._psycopg2_url_and_connect_args(
        "postgresql+asyncpg://u:p@h/db?sslmode=verify-full"
    )
    assert connect_args == {}
    assert dict(url.query) == {"sslmode": "verify-full"}


def test_psycopg2_url_drops_bare_ssl_when_sslmode_already_present():
    url, connect_args = copper_writer._psycopg2_url_and_connect_args(
        "postgresql+asyncpg://u:p@h/db?ssl=require&sslmode=verify-full"
    )
    assert connect_args == {}
    assert dict(url.query) == {"sslmode": "verify-full"}


def test_sync_engine_passes_normalized_url_and_sslmode_to_create_engine(monkeypatch):
    """End-to-end through _sync_engine(): confirms the psycopg2 engine is
    actually built with the translated sslmode, not just that the helper
    function computes it correctly."""
    monkeypatch.setattr(
        copper_writer.settings,
        "database_url",
        "postgresql+asyncpg://u:p@h/db?ssl=require",
    )

    captured = {}

    class _FakeEngine:
        pass

    def fake_create_engine(url, pool_pre_ping=None, connect_args=None):
        captured["url"] = str(url)
        captured["pool_pre_ping"] = pool_pre_ping
        captured["connect_args"] = connect_args
        return _FakeEngine()

    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

    engine = copper_writer._sync_engine()

    assert isinstance(engine, _FakeEngine)
    assert captured["connect_args"] == {"sslmode": "require"}
    assert captured["pool_pre_ping"] is True
    assert "ssl=" not in captured["url"]
    assert captured["url"].startswith("postgresql://")


def test_outbox_health_403s_for_non_admin():
    async def _fake_get_db():
        yield _FakeOutboxHealthSession(count_rows=[], failed_rows=[])

    app.dependency_overrides[get_current_user] = _fake_non_admin_user
    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = client.get("/api/v1/leads/outbox-health")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 403
