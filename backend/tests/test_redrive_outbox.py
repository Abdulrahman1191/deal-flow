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
import re
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


def _fake_row(redrive_count=0, last_error=None, row_id=None, created_at=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=row_id or uuid.uuid4(),
        endpoint="/leads/123",
        status="failed",
        attempts=5,
        next_attempt_at=now - timedelta(minutes=30),
        last_error=last_error,
        redrive_count=redrive_count,
        created_at=created_at or (now - timedelta(minutes=30)),
        updated_at=now - timedelta(minutes=30),
    )


class _FilteringFakeSession:
    """More faithful than _FakeRedriveSession -- actually applies the
    query's status/redrive_count WHERE and LIMIT against the full row list
    (by reading them out of the compiled SQL), instead of just handing back
    whatever rows it was constructed with. Used to prove the SQL-level
    exclusion of at/over-cap rows actually keeps them out of the working
    set, rather than only relying on the Python-side skip."""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        sql = str(query.compile(compile_kwargs={"literal_binds": True}))
        cap_match = re.search(r"redrive_count < (\d+)", sql)
        assert cap_match, "expected a redrive_count < <cap> filter in the query"
        cap = int(cap_match.group(1))
        limit_match = re.search(r"LIMIT (\d+)", sql)
        limit = int(limit_match.group(1)) if limit_match else len(self._rows)

        eligible = [r for r in self._rows if r.status == "failed" and r.redrive_count < cap]
        eligible.sort(key=lambda r: r.created_at)
        return _FakeRowsResult(eligible[:limit])

    async def commit(self):
        pass


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
    result = _run_redrive(monkeypatch, [row], max_redrives=3)

    assert row.status == "failed"
    # Stamped to the cap (not left at 0) so the query's WHERE clause excludes
    # it on every future run too -- otherwise this row would sit at the front
    # of the created_at-ordered batch forever and starve newer failures.
    assert row.redrive_count == 3
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


def test_config_unset_skip_reason_is_treated_as_terminal(monkeypatch):
    """_record_skipped_write's 'config id unset' rows were never attempted --
    body_json is {} because the payload was never built, so re-driving can
    never perform the originally intended write. Resetting one to 'pending'
    would let drain_copper_outbox_task issue a real empty PUT that could
    succeed and flip the row to 'done', silently erasing the config-unset
    signal that GET /leads/outbox-health (#65) depends on. Must be skipped
    like any other terminal error, not redriven."""
    row = _fake_row(last_error="skipped archive_in_copper: copper_unqualified_status_id unset")
    result = _run_redrive(monkeypatch, [row], max_redrives=3)

    assert row.status == "failed"
    assert row.redrive_count == 3
    assert result == {"checked": 1, "redriven": 0, "skipped_terminal": 1, "skipped_capped": 0}


def test_mixed_batch_applies_each_guard_independently(monkeypatch):
    under_cap = _fake_row(redrive_count=1)
    at_cap = _fake_row(redrive_count=3)
    terminal = _fake_row(last_error="Client error '404 Not Found' for url 'https://api.copper.com/leads/9'")

    result = _run_redrive(monkeypatch, [under_cap, at_cap, terminal], max_redrives=3)

    assert under_cap.status == "pending"
    assert at_cap.status == "failed"
    assert terminal.status == "failed"
    assert result == {"checked": 3, "redriven": 1, "skipped_terminal": 1, "skipped_capped": 1}


def test_query_excludes_rows_at_or_over_cap(monkeypatch):
    """SQL-level guard (issue #131 follow-up): the query itself must exclude
    rows at/over the redrive cap, not just skip them in Python after
    fetching -- otherwise a batch of BATCH_SIZE dead rows would occupy the
    entire created_at-ordered query forever (nothing else ever purges or
    otherwise updates a skipped row), starving every newer redrivable
    failure behind them."""
    at_cap = _fake_row(redrive_count=3, created_at=datetime.now(timezone.utc) - timedelta(days=1))
    redrivable = _fake_row(redrive_count=0, created_at=datetime.now(timezone.utc))

    monkeypatch.setattr(redrive_outbox.settings, "outbox_max_redrives", 3)
    monkeypatch.setattr(
        redrive_outbox, "CelerySessionLocal", lambda: _FilteringFakeSession([at_cap, redrivable])
    )

    result = asyncio.run(redrive_outbox._run())

    assert redrivable.status == "pending"
    assert at_cap.status == "failed"
    assert result == {"checked": 1, "redriven": 1, "skipped_terminal": 0, "skipped_capped": 0}


def test_newer_redrivable_row_is_not_starved_by_older_dead_rows(monkeypatch):
    """Reproduces the batch-starvation scenario from the #131 follow-up
    review: BATCH_SIZE older rows already at the redrive cap, ordered first
    by created_at, plus one newer redrivable row that would fall past the
    batch if the dead rows weren't excluded from the query. Without the
    SQL-level exclusion, the dead rows would fill the entire 50-row batch
    and the newer row would never be selected."""
    base = datetime.now(timezone.utc) - timedelta(days=1)
    dead_rows = [
        _fake_row(redrive_count=3, created_at=base + timedelta(seconds=i))
        for i in range(redrive_outbox.BATCH_SIZE)
    ]
    fresh_row = _fake_row(redrive_count=0, created_at=base + timedelta(hours=1))

    monkeypatch.setattr(redrive_outbox.settings, "outbox_max_redrives", 3)
    monkeypatch.setattr(
        redrive_outbox, "CelerySessionLocal", lambda: _FilteringFakeSession(dead_rows + [fresh_row])
    )

    result = asyncio.run(redrive_outbox._run())

    assert fresh_row.status == "pending"
    assert result["redriven"] == 1
    assert all(r.status == "failed" for r in dead_rows)


def test_terminal_row_is_stamped_to_leave_working_set(monkeypatch):
    """A row that hits the terminal-error guard must be stamped to the cap
    immediately (not left at its original redrive_count) so the SQL WHERE
    excludes it on every subsequent run -- otherwise a terminal row would
    occupy a batch slot forever, the same starvation this task exists to
    prevent for capped rows."""
    row = _fake_row(redrive_count=0, last_error="Client error '404 Not Found' for url '...'")
    _run_redrive(monkeypatch, [row], max_redrives=5)

    assert row.status == "failed"
    assert row.redrive_count == 5


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
    assert redrive_outbox._is_terminal_error(
        "skipped archive_in_copper: copper_unqualified_status_id unset"
    ) is True
    assert redrive_outbox._is_terminal_error(
        "skipped reject_in_copper: copper_unqualified_status_id unset"
    ) is True
