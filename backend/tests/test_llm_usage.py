"""
Tests for recording DeepSeek token usage per call (issue #191).

`claude_agent._chat_completion` is the single choke point every DeepSeek call
goes through; it now records one app.services.llm_usage row per call via a
sync engine (same best-effort, DB-free-in-CI pattern as
test_assess_lead_failure_reason.py's _FakeConn/_FakeEngine -- no live
Postgres is available in this CI). GET /api/v1/ops/llm-usage is exercised
with the TestClient + dependency-override pattern used throughout
test_ops_endpoint.py / test_associates_performance.py.
"""
from __future__ import annotations
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIStatusError

from app.main import app
from app.routers import ops
from app.services import claude_agent, llm_breaker, llm_usage
from app.services.auth import get_current_user

client = TestClient(app)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self):
        self.statements: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.statements.append((str(stmt), params or {}))
        return SimpleNamespace(rowcount=0)


class _FakeEngine:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def begin(self):
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc_info):
        return False


def _patch_usage_engine(monkeypatch) -> _FakeConn:
    conn = _FakeConn()
    monkeypatch.setattr(llm_usage, "create_engine", lambda *a, **k: _FakeEngine(conn))
    return conn


class _FakeRedis:
    def __init__(self):
        self.store, self.ttls = {}, {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value.encode()
        self.ttls[key] = ex


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(llm_breaker, "_redis", r)
    monkeypatch.setattr(llm_breaker, "_local_open_until", 0.0)
    monkeypatch.setattr(llm_breaker.settings, "llm_outage_pause_seconds", 900)
    return r


class _FakeDeepSeek:
    def __init__(self, error=None, reply='{"is_match": true}', usage=None):
        self.error, self.reply, self.calls = error, reply, 0
        self.usage = usage or SimpleNamespace(prompt_tokens=123, completion_tokens=45, total_tokens=168)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
            usage=self.usage,
        )


def _use_deepseek(monkeypatch, fake):
    monkeypatch.setattr(claude_agent, "_get_client", lambda: fake)


def _status_error(code, message="Insufficient Balance"):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    body = {"error": {"message": message}}
    response = httpx.Response(code, request=request, json=body)
    return APIStatusError(f"Error code: {code} - {body}", response=response, body=body)


def _only_insert(conn: _FakeConn) -> dict:
    inserts = [p for sql, p in conn.statements if "INSERT INTO llm_usage" in sql]
    assert len(inserts) == 1, f"expected exactly one llm_usage insert, got {len(inserts)}"
    return inserts[0]


# ---------------------------------------------------------------------------
# 1. successful call writes exactly one row, status="ok", tokens from response
# ---------------------------------------------------------------------------

def test_successful_call_writes_one_ok_row_with_tokens_from_response(monkeypatch, fake_redis):
    conn = _patch_usage_engine(monkeypatch)
    _use_deepseek(monkeypatch, _FakeDeepSeek())

    result = claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text", lead_id="lead-1")

    assert result is True
    row = _only_insert(conn)
    assert row["purpose"] == "verify_deck"
    assert row["lead_id"] == "lead-1"
    assert row["status"] == "ok"
    assert row["prompt_tokens"] == 123
    assert row["completion_tokens"] == 45
    assert row["total_tokens"] == 168


# ---------------------------------------------------------------------------
# 2. a call that raises writes status="error" and does not swallow the error
# ---------------------------------------------------------------------------

def test_a_raising_call_writes_an_error_row_and_still_raises(monkeypatch, fake_redis):
    conn = _patch_usage_engine(monkeypatch)
    _use_deepseek(monkeypatch, _FakeDeepSeek(error=_status_error(500, "server error")))

    with pytest.raises(APIStatusError):
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")

    row = _only_insert(conn)
    assert row["status"] == "error"
    assert row["prompt_tokens"] == 0 and row["total_tokens"] == 0


def test_an_account_level_failure_also_writes_an_error_row_and_raises_unavailable(monkeypatch, fake_redis):
    conn = _patch_usage_engine(monkeypatch)
    _use_deepseek(monkeypatch, _FakeDeepSeek(error=_status_error(402)))

    with pytest.raises(llm_breaker.LLMUnavailable):
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")

    row = _only_insert(conn)
    assert row["status"] == "error"


# ---------------------------------------------------------------------------
# 3. a call refused by an open breaker writes status="breaker_open", zero
#    tokens, and still raises LLMUnavailable without reaching DeepSeek
# ---------------------------------------------------------------------------

def test_breaker_open_writes_a_breaker_open_row_with_zero_tokens_and_still_raises(monkeypatch, fake_redis):
    llm_breaker.open_breaker("DeepSeek 402: Insufficient Balance")
    conn = _patch_usage_engine(monkeypatch)
    fake = _FakeDeepSeek()
    _use_deepseek(monkeypatch, fake)

    with pytest.raises(llm_breaker.LLMUnavailable):
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")

    assert fake.calls == 0
    row = _only_insert(conn)
    assert row["status"] == "breaker_open"
    assert row["prompt_tokens"] == 0 and row["completion_tokens"] == 0 and row["total_tokens"] == 0


# ---------------------------------------------------------------------------
# 4. a DB failure while recording usage never fails the call it's measuring
# ---------------------------------------------------------------------------

def test_a_db_failure_recording_usage_does_not_fail_the_call(monkeypatch, fake_redis):
    def _broken_engine(*a, **k):
        raise ConnectionError("db unreachable")

    monkeypatch.setattr(llm_usage, "create_engine", _broken_engine)
    _use_deepseek(monkeypatch, _FakeDeepSeek())

    # The call completes normally even though every llm_usage write fails.
    assert claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text") is True


def test_llm_usage_record_never_raises_on_a_db_failure():
    llm_usage.record(purpose="verify_deck", status="ok", model="deepseek-chat", duration_ms=1)


# ---------------------------------------------------------------------------
# GET /api/v1/ops/llm-usage
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeUsageSession:
    """Answers the five queries llm_usage() issues in order: totals_today,
    totals_7d, totals_30d, by_purpose, top_leads."""

    def __init__(self, today_row, d7_row, d30_row, by_purpose_rows, top_lead_rows=()):
        self._results = [
            _FakeResult([today_row]),
            _FakeResult([d7_row]),
            _FakeResult([d30_row]),
            _FakeResult(by_purpose_rows),
            _FakeResult(list(top_lead_rows)),
        ]

    async def execute(self, *_a, **_k):
        return self._results.pop(0)


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(ops.get_db, None)


def test_llm_usage_is_forbidden_for_non_admin():
    _auth_as("waleed@raed.vc")
    try:
        response = client.get("/api/v1/ops/llm-usage")
    finally:
        _clear_auth()
    assert response.status_code == 403


def test_llm_usage_returns_today_7d_30d_totals_and_per_purpose_breakdown(monkeypatch):
    async def _fake_get_db():
        yield _FakeUsageSession(
            today_row=(2, 8_000, 1_000, 9_000),
            d7_row=(10, 50_000, 5_000, 55_000),
            d30_row=(40, 200_000, 20_000, 220_000),
            by_purpose_rows=[("assess", 7, 48_000, 4_500, 52_500), ("verify_deck", 3, 2_000, 500, 2_500)],
        )

    app.dependency_overrides[ops.get_db] = _fake_get_db
    _auth_as("abdulrahman@raed.vc")  # default owner_email -> is_owner() True
    try:
        response = client.get("/api/v1/ops/llm-usage?days=30")
    finally:
        _clear_auth()

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 30
    assert body["totals_today"] == {
        "calls": 2, "prompt_tokens": 8_000, "completion_tokens": 1_000, "total_tokens": 9_000,
    }
    assert body["totals_7d"] == {
        "calls": 10, "prompt_tokens": 50_000, "completion_tokens": 5_000, "total_tokens": 55_000,
    }
    assert body["totals_30d"] == {
        "calls": 40, "prompt_tokens": 200_000, "completion_tokens": 20_000, "total_tokens": 220_000,
    }
    assert body["by_purpose"][0]["purpose"] == "assess"
    assert body["by_purpose"][0]["calls"] == 7
    # share is this purpose's total_tokens over the window's combined total
    # (52_500 + 2_500 = 55_000) -- attribution across purposes, not just a count.
    assert body["by_purpose"][0]["share"] == pytest.approx(52_500 / 55_000, abs=1e-4)
    assert body["by_purpose"][1]["purpose"] == "verify_deck"
    assert body["by_purpose"][1]["share"] == pytest.approx(2_500 / 55_000, abs=1e-4)


def test_llm_usage_returns_top_leads_by_token_spend(monkeypatch):
    async def _fake_get_db():
        yield _FakeUsageSession(
            today_row=(0, 0, 0, 0),
            d7_row=(0, 0, 0, 0),
            d30_row=(0, 0, 0, 0),
            by_purpose_rows=[("assess", 2, 9_000, 1_000, 10_000)],
            top_lead_rows=[("lead-costly", 2, 7_000), ("lead-cheap", 1, 3_000)],
        )

    app.dependency_overrides[ops.get_db] = _fake_get_db
    _auth_as("abdulrahman@raed.vc")
    try:
        response = client.get("/api/v1/ops/llm-usage")
    finally:
        _clear_auth()

    assert response.status_code == 200
    body = response.json()
    assert body["top_leads"] == [
        {"lead_id": "lead-costly", "calls": 2, "total_tokens": 7_000},
        {"lead_id": "lead-cheap", "calls": 1, "total_tokens": 3_000},
    ]


def test_llm_usage_by_purpose_share_is_zero_when_window_is_empty(monkeypatch):
    async def _fake_get_db():
        yield _FakeUsageSession(
            today_row=(0, 0, 0, 0), d7_row=(0, 0, 0, 0), d30_row=(0, 0, 0, 0), by_purpose_rows=[],
        )

    app.dependency_overrides[ops.get_db] = _fake_get_db
    _auth_as("abdulrahman@raed.vc")
    try:
        response = client.get("/api/v1/ops/llm-usage")
    finally:
        _clear_auth()

    assert response.status_code == 200
    assert response.json()["by_purpose"] == []


# ---------------------------------------------------------------------------
# Retention cut-off (issue #191): llm_usage rows older than the configured
# default must not grow the table unbounded.
# ---------------------------------------------------------------------------

def test_purge_older_than_deletes_rows_before_the_cutoff(monkeypatch):
    conn = _patch_usage_engine(monkeypatch)

    rowcount = llm_usage.purge_older_than(90)

    deletes = [(sql, params) for sql, params in conn.statements if "DELETE FROM llm_usage" in sql]
    assert len(deletes) == 1
    assert "created_at < :cutoff" in deletes[0][0]
    assert rowcount == 0  # _FakeConn.execute doesn't set a result object


def test_purge_older_than_never_raises_on_a_db_failure(monkeypatch):
    def _broken_engine(*a, **k):
        raise ConnectionError("db unreachable")

    monkeypatch.setattr(llm_usage, "create_engine", _broken_engine)

    assert llm_usage.purge_older_than(90) == -1


def test_dedupe_leads_task_also_purges_old_llm_usage_rows(monkeypatch):
    from app.tasks import dedupe_leads as dedupe_leads_task_module

    class _FakeDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    async def _fake_dedupe(db, commit):
        return {"active": 0, "groups": 0, "to_archive": 0, "archived": 0}

    monkeypatch.setattr(dedupe_leads_task_module, "CelerySessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(dedupe_leads_task_module, "dedupe_leads", _fake_dedupe)

    purge_calls = []
    monkeypatch.setattr(
        dedupe_leads_task_module.llm_usage, "purge_older_than",
        lambda days: purge_calls.append(days) or 3,
    )

    result = dedupe_leads_task_module.dedupe_leads_task()

    assert purge_calls == [dedupe_leads_task_module.settings.llm_usage_retention_days]
    assert result["llm_usage_purged"] == 3
