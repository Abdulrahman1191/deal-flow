"""
Tests for the copper_outbox re-drive backstop (issue #131).

redrive_outbox._run() must reset a 'failed' row back to 'pending' (so
drain_copper_outbox_task picks it up again) unless it's already hit the
redrive cap or its last_error indicates the write can never succeed -- and
must never touch anything that isn't 'failed'. Exercised against a fake
CelerySessionLocal async-context-manager session (mirrors the
_FakeRunSession pattern in test_reap_stuck_leads.py) so no live Postgres is
needed.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.copper_outbox import CopperOutbox
from app.tasks import redrive_outbox


class _FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeRedriveSession:
    """Stands in for CelerySessionLocal()'s async context manager -- the
    only query _run() issues is select(CopperOutbox), so every execute()
    answers with the fixed `rows` list."""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        assert entity is CopperOutbox
        return _FakeRowsResult(self._rows)

    async def commit(self):
        pass


def _fake_row(redrive_count=0, last_error=None, row_id=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=row_id or uuid.uuid4(),
        endpoint="/leads/123",
        status="failed",
        attempts=5,
        next_attempt_at=now - timedelta(minutes=30),
        last_error=last_error,
        redrive_count=redrive_count,
        updated_at=now - timedelta(minutes=30),
    )


def _run_redrive(monkeypatch, rows, max_redrives=3):
    monkeypatch.setattr(redrive_outbox.settings, "outbox_max_redrives", max_redrives)
    monkeypatch.setattr(redrive_outbox, "CelerySessionLocal", lambda: _FakeRedriveSession(rows))

    return asyncio.run(redrive_outbox._run())


def test_failed_row_under_cap_is_reset_to_pending(monkeypatch):
    row = _fake_row(redrive_count=0)
    result = _run_redrive(monkeypatch, [row])

    assert row.status == "pending"
    assert row.attempts == 0
    assert row.redrive_count == 1
    assert row.next_attempt_at <= datetime.now(timezone.utc)
    assert result == {"checked": 1, "redriven": 1, "skipped_terminal": 0, "skipped_capped": 0}


def test_row_at_cap_stays_failed(monkeypatch):
    row = _fake_row(redrive_count=3)
    result = _run_redrive(monkeypatch, [row], max_redrives=3)

    assert row.status == "failed"
    assert row.redrive_count == 3
    assert result == {"checked": 1, "redriven": 0, "skipped_terminal": 0, "skipped_capped": 1}


def test_row_past_cap_stays_failed(monkeypatch):
    row = _fake_row(redrive_count=5)
    result = _run_redrive(monkeypatch, [row], max_redrives=3)

    assert row.status == "failed"
    assert result["skipped_capped"] == 1


def test_404_row_is_not_redriven(monkeypatch):
    row = _fake_row(last_error="Client error '404 Not Found' for url 'https://api.copper.com/leads/123'")
    result = _run_redrive(monkeypatch, [row])

    assert row.status == "failed"
    assert row.redrive_count == 0
    assert result == {"checked": 1, "redriven": 0, "skipped_terminal": 1, "skipped_capped": 0}


def test_other_terminal_client_error_is_not_redriven(monkeypatch):
    """A non-404 4xx (e.g. 400 bad request) is just as unrecoverable as a
    404 -- retrying the exact same payload will never succeed."""
    row = _fake_row(last_error="Client error '400 Bad Request' for url 'https://api.copper.com/leads/123'")
    result = _run_redrive(monkeypatch, [row])

    assert row.status == "failed"
    assert result["skipped_terminal"] == 1


def test_transient_server_error_is_redriven(monkeypatch):
    """A 5xx (Copper's own outage) is exactly the case this task exists
    for -- must not be mistaken for a terminal client error."""
    row = _fake_row(last_error="Server error '503 Service Unavailable' for url 'https://api.copper.com/leads/123'")
    result = _run_redrive(monkeypatch, [row])

    assert row.status == "pending"
    assert row.redrive_count == 1
    assert result["redriven"] == 1


def test_rate_limited_and_timeout_are_treated_as_transient(monkeypatch):
    """408/429 are excluded from the terminal-error set even though they're
    nominally 4xx -- a retry can plausibly succeed once the rate limit/timeout
    clears."""
    rate_limited = _fake_row(last_error="Client error '429 Too Many Requests' for url 'https://api.copper.com/leads/1'")
    timed_out = _fake_row(last_error="Client error '408 Request Timeout' for url 'https://api.copper.com/leads/2'")

    result = _run_redrive(monkeypatch, [rate_limited, timed_out])

    assert rate_limited.status == "pending"
    assert timed_out.status == "pending"
    assert result == {"checked": 2, "redriven": 2, "skipped_terminal": 0, "skipped_capped": 0}


def test_config_unset_skip_reason_is_not_treated_as_terminal(monkeypatch):
    """_record_skipped_write's 'config id unset' rows have no HTTP status at
    all -- they shouldn't match the terminal-error heuristic (no 4xx code, no
    'not found' text), so they're redriven like any other under-cap failure."""
    row = _fake_row(last_error="skipped archive_in_copper: copper_unqualified_status_id unset")
    result = _run_redrive(monkeypatch, [row])

    assert row.status == "pending"
    assert result["redriven"] == 1


def test_mixed_batch_applies_each_guard_independently(monkeypatch):
    under_cap = _fake_row(redrive_count=1)
    at_cap = _fake_row(redrive_count=3)
    terminal = _fake_row(last_error="Client error '404 Not Found' for url 'https://api.copper.com/leads/9'")

    result = _run_redrive(monkeypatch, [under_cap, at_cap, terminal], max_redrives=3)

    assert under_cap.status == "pending"
    assert at_cap.status == "failed"
    assert terminal.status == "failed"
    assert result == {"checked": 3, "redriven": 1, "skipped_terminal": 1, "skipped_capped": 1}


def test_zero_max_redrives_falls_back_to_default(monkeypatch):
    """A misconfigured 0 (or unset) outbox_max_redrives must not turn into
    'never redrive anything' -- it should fall back to DEFAULT_MAX_REDRIVES."""
    row = _fake_row(redrive_count=0)
    result = _run_redrive(monkeypatch, [row], max_redrives=0)

    assert row.status == "pending"
    assert result["redriven"] == 1


def test_is_terminal_error_helper():
    assert redrive_outbox._is_terminal_error(None) is False
    assert redrive_outbox._is_terminal_error("") is False
    assert redrive_outbox._is_terminal_error("Client error '404 Not Found' for url '...'") is True
    assert redrive_outbox._is_terminal_error("Client error '429 Too Many Requests' for url '...'") is False
    assert redrive_outbox._is_terminal_error("Server error '500 Internal Server Error' for url '...'") is False
    assert redrive_outbox._is_terminal_error("Connection refused") is False
    assert redrive_outbox._is_terminal_error("404 - lead not found in Copper") is True
