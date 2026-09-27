"""
Tests for scripts/close_duplicates_in_copper.py (issue #178).

decide_action() is a pure function (mirrors the plan_cleanup()/build_reconciliation()
test pattern -- no live DB, no live Copper) covering every branch the acceptance
criteria call out: close, 404/already-Unqualified/not-open skips, and the
hidden-lead "surviving twin missing" needs_review case.

gather_rows()/find_duplicate_leads() are exercised with a fake AsyncSession
(mirrors test_dedup.py's _FakeSession -- entity-routed execute(), plus a
get() for the canonical lookup) and a monkeypatched fetch_lead_by_id, so no
live Postgres or Copper is needed.

apply_plan()/main() are tested with copper_writer._enqueue mocked, confirming
--commit enqueues exactly one outbox row per "close" decision and dry-run
enqueues nothing -- mirrors the mocking pattern in test_copper_writebacks.py
and test_close_stale_copper.py.
"""
from __future__ import annotations
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import close_duplicates_in_copper as cdc  # noqa: E402

from app.models.event import LeadEvent  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.services import copper_writer  # noqa: E402


# --- fakes -------------------------------------------------------------------

def _lead(company_name="DupCo", copper_id="100", status="archived", lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        company_name=company_name,
        copper_id=copper_id,
        status=status,
    )


def _event(lead_id, reason="duplicate", canonical=None, created_at=None):
    return SimpleNamespace(
        lead_id=lead_id,
        payload={"reason": reason, **({"canonical": canonical} if canonical is not None else {})},
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _copper_lead(cid, status_id=737640, tags=None, company_name=None):
    return {"id": cid, "status_id": status_id, "tags": tags or [], "company_name": company_name}


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Routes execute() by query target entity (mirrors test_dedup.py's
    _FakeSession): the archived-leads list for `select(Lead)...`, the events
    list for `select(LeadEvent)...`. get() backs the canonical-lead lookup."""

    def __init__(self, leads, events, canonical_by_id=None):
        self._leads = list(leads)
        self._events = list(events)
        self._canonical_by_id = canonical_by_id or {}

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _ScalarsResult(self._leads)
        assert entity is LeadEvent
        return _ScalarsResult(self._events)

    async def get(self, model, pk):
        assert model is Lead
        return self._canonical_by_id.get(pk)


def _run_gather(leads, events, canonical_by_id=None, limit=None):
    session = _FakeSession(leads, events, canonical_by_id)
    return asyncio.run(cdc.gather_rows(session, limit=limit))


# --- decide_action (pure) -----------------------------------------------------

def test_decide_action_closes_when_duplicate_open_and_surviving_live(monkeypatch):
    monkeypatch.setattr(cdc.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)

    decision = cdc.decide_action(
        _copper_lead("100", status_id=737640),
        _copper_lead("200", status_id=737640),
        "Acme Co", "200",
    )

    assert decision["action"] == "close"
    assert decision["detail_text"] == "Duplicate of Acme Co (Copper #200)"


def test_decide_action_skips_on_404():
    decision = cdc.decide_action(None, _copper_lead("200"), "Acme Co", "200")
    assert decision == {"action": "skip", "reason": "Copper record 404 (already deleted/merged)"}


def test_decide_action_skips_when_already_unqualified(monkeypatch):
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)
    decision = cdc.decide_action(
        _copper_lead("100", status_id=737642), _copper_lead("200"), "Acme Co", "200",
    )
    assert decision["action"] == "skip"
    assert "already Unqualified" in decision["reason"]


def test_decide_action_skips_when_not_in_open_status(monkeypatch):
    monkeypatch.setattr(cdc.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)
    decision = cdc.decide_action(
        _copper_lead("100", status_id=999999), _copper_lead("200"), "Acme Co", "200",
    )
    assert decision["action"] == "skip"
    assert "not in open status" in decision["reason"]


def test_decide_action_needs_review_when_surviving_missing(monkeypatch):
    """The hidden-lead pattern: the surviving twin's own Copper record is
    gone. Must be reported, never "fixed" by closing the only live record."""
    monkeypatch.setattr(cdc.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)
    decision = cdc.decide_action(
        _copper_lead("100", status_id=737640), None, "Acme Co", "200",
    )
    assert decision["action"] == "needs_review"
    assert "200" in decision["reason"]
    assert "detail_text" not in decision


# --- gather_rows / find_duplicate_leads (fake DB + mocked Copper fetch) ------

def test_gather_rows_marks_open_duplicate_for_close(monkeypatch):
    monkeypatch.setattr(cdc.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)

    canonical = _lead("Acme Co", copper_id="200", status="pending")
    dup = _lead("Acme Co (dup)", copper_id="100", status="archived")
    events = [_event(dup.id, reason="duplicate", canonical=str(canonical.id))]

    copper_records = {"100": _copper_lead("100", status_id=737640), "200": _copper_lead("200", status_id=737640)}
    monkeypatch.setattr(cdc, "fetch_lead_by_id", lambda cid: copper_records[cid])

    rows = _run_gather([dup], events, canonical_by_id={canonical.id: canonical})

    assert len(rows) == 1
    assert rows[0]["decision"]["action"] == "close"
    assert rows[0]["decision"]["detail_text"] == "Duplicate of Acme Co (Copper #200)"
    assert rows[0]["copper_id"] == "100"


def test_gather_rows_ignores_leads_whose_latest_archive_reason_is_not_duplicate():
    lead = _lead("RejectedCo", copper_id="100", status="archived")
    events = [_event(lead.id, reason="rejection")]

    rows = _run_gather([lead], events)
    assert rows == []


def test_gather_rows_needs_review_when_canonical_row_is_missing():
    """The event's canonical uuid points at a lead row that no longer exists
    -- can't resolve a surviving twin at all, so report for manual review
    rather than guessing."""
    dup = _lead("Acme Co (dup)", copper_id="100", status="archived")
    missing_canonical_id = uuid.uuid4()
    events = [_event(dup.id, reason="duplicate", canonical=str(missing_canonical_id))]

    rows = _run_gather([dup], events, canonical_by_id={})

    assert len(rows) == 1
    assert rows[0]["decision"]["action"] == "needs_review"


def test_gather_rows_needs_review_when_surviving_copper_record_is_gone(monkeypatch):
    """Surviving lead row exists in the DB, but its Copper record 404s --
    the hidden-lead pattern. Must not close the duplicate."""
    monkeypatch.setattr(cdc.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)

    canonical = _lead("Acme Co", copper_id="200", status="pending")
    dup = _lead("Acme Co (dup)", copper_id="100", status="archived")
    events = [_event(dup.id, reason="duplicate", canonical=str(canonical.id))]

    def fake_fetch(cid):
        return _copper_lead("100", status_id=737640) if cid == "100" else None

    monkeypatch.setattr(cdc, "fetch_lead_by_id", fake_fetch)

    rows = _run_gather([dup], events, canonical_by_id={canonical.id: canonical})

    assert rows[0]["decision"]["action"] == "needs_review"


def test_gather_rows_skips_duplicate_already_404(monkeypatch):
    canonical = _lead("Acme Co", copper_id="200", status="pending")
    dup = _lead("Acme Co (dup)", copper_id="100", status="archived")
    events = [_event(dup.id, reason="duplicate", canonical=str(canonical.id))]

    monkeypatch.setattr(cdc, "fetch_lead_by_id", lambda cid: None if cid == "100" else _copper_lead("200"))

    rows = _run_gather([dup], events, canonical_by_id={canonical.id: canonical})

    assert rows[0]["decision"] == {"action": "skip", "reason": "Copper record 404 (already deleted/merged)"}


def test_gather_rows_idempotent_second_run_finds_nothing_to_close(monkeypatch):
    """Re-running after a successful pass: the duplicate's Copper record is
    now Unqualified, so it's skipped -- nothing enqueued."""
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)

    canonical = _lead("Acme Co", copper_id="200", status="pending")
    dup = _lead("Acme Co (dup)", copper_id="100", status="archived")
    events = [_event(dup.id, reason="duplicate", canonical=str(canonical.id))]

    monkeypatch.setattr(
        cdc, "fetch_lead_by_id",
        lambda cid: _copper_lead("100", status_id=737642) if cid == "100" else _copper_lead("200"),
    )

    rows = _run_gather([dup], events, canonical_by_id={canonical.id: canonical})

    assert rows[0]["decision"]["action"] == "skip"
    assert [r for r in rows if r["decision"]["action"] == "close"] == []


def test_gather_rows_one_lead_error_does_not_abort_the_rest(monkeypatch):
    canonical = _lead("Acme Co", copper_id="200", status="pending")
    dup1 = _lead("BrokenCo", copper_id="100", status="archived")
    dup2 = _lead("GoodCo", copper_id="101", status="archived")
    events = [
        _event(dup1.id, reason="duplicate", canonical=str(canonical.id)),
        _event(dup2.id, reason="duplicate", canonical=str(canonical.id)),
    ]

    def fake_fetch(cid):
        if cid == "100":
            raise RuntimeError("copper 500")
        return _copper_lead(cid, status_id=737640)

    monkeypatch.setattr(cdc, "fetch_lead_by_id", fake_fetch)
    monkeypatch.setattr(cdc.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)

    rows = _run_gather([dup1, dup2], events, canonical_by_id={canonical.id: canonical})

    by_copper_id = {r["copper_id"]: r for r in rows}
    assert by_copper_id["100"]["decision"]["action"] == "skip"
    assert "copper 500" in by_copper_id["100"]["decision"]["reason"]
    assert by_copper_id["101"]["decision"]["action"] == "close"


def test_gather_rows_limit_bounds_candidates():
    canonical = _lead("Acme Co", copper_id="200", status="pending")
    dups = [_lead(f"Co{i}", copper_id=str(100 + i), status="archived") for i in range(5)]
    events = [_event(d.id, reason="duplicate", canonical=str(canonical.id)) for d in dups]

    rows = asyncio.run(
        cdc.gather_rows(_FakeSession(dups, events, {canonical.id: canonical}), limit=2)
    )
    assert len(rows) == 2


# --- apply_plan ---------------------------------------------------------------

def test_apply_plan_enqueues_exactly_one_outbox_row_per_close(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 737642)

    enqueued = []
    monkeypatch.setattr(
        copper_writer, "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(
            {"copper_id": copper_id, "endpoint": endpoint, "body": body, "method": method}
        ),
    )

    to_close = [{
        "company_name": "Acme Co (dup)", "copper_id": "100", "existing_tags": ["some-tag"],
        "decision": {"action": "close", "detail_text": "Duplicate of Acme Co (Copper #200)"},
    }]
    result = cdc.apply_plan(to_close)

    assert result["closed"] == ["100"]
    assert len(enqueued) == 1
    call = enqueued[0]
    assert call["copper_id"] == "100"
    assert call["endpoint"] == "/leads/100"
    assert call["method"] == "PUT"
    assert call["body"]["status_id"] == 737642
    assert "raed:duplicate" in call["body"]["tags"]
    assert "some-tag" in call["body"]["tags"]


def test_apply_plan_includes_detail_text_in_custom_fields_when_configured(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 737642)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_detail_id", 244359)
    monkeypatch.setattr(copper_writer.settings, "copper_cf_unqual_reason_id", 244358)

    enqueued = []
    monkeypatch.setattr(
        copper_writer, "_enqueue",
        lambda copper_id, endpoint, body, method="PUT": enqueued.append(body),
    )

    to_close = [{
        "company_name": "Acme Co (dup)", "copper_id": "100", "existing_tags": [],
        "decision": {"action": "close", "detail_text": "Duplicate of Acme Co (Copper #200)"},
    }]
    cdc.apply_plan(to_close)

    custom_fields = enqueued[0]["custom_fields"]
    assert custom_fields == [
        {"custom_field_definition_id": 244359, "value": "Duplicate of Acme Co (Copper #200)"}
    ]


def test_apply_plan_one_failure_does_not_abort_the_rest(monkeypatch):
    monkeypatch.setattr(copper_writer.settings, "copper_unqualified_status_id", 737642)

    def fake_close(copper_id, existing_tags, detail_text):
        if copper_id == "100":
            raise RuntimeError("copper down")
        return "outbox-row-id"

    monkeypatch.setattr(copper_writer, "close_duplicate_in_copper", fake_close)

    to_close = [
        {"company_name": "A", "copper_id": "100", "existing_tags": [], "decision": {"action": "close", "detail_text": "x"}},
        {"company_name": "B", "copper_id": "101", "existing_tags": [], "decision": {"action": "close", "detail_text": "y"}},
    ]
    result = cdc.apply_plan(to_close)

    assert result["failed"] == ["100"]
    assert result["closed"] == ["101"]


# --- main(): env guard, dry-run vs --commit ----------------------------------

def _configure_env(monkeypatch):
    monkeypatch.setattr(cdc.settings, "copper_api_key", "k")
    monkeypatch.setattr(cdc.settings, "copper_user_email", "u@raed.vc")
    monkeypatch.setattr(cdc.settings, "copper_open_status_id", 737640)
    monkeypatch.setattr(cdc.settings, "copper_unqualified_status_id", 737642)


def _stub_fetch_plan(monkeypatch, plan):
    async def fake_fetch_plan(limit):
        return plan

    monkeypatch.setattr(cdc, "_fetch_plan", fake_fetch_plan)


def test_main_refuses_without_copper_env(monkeypatch, capsys):
    monkeypatch.setattr(cdc.settings, "copper_api_key", "")

    with pytest.raises(SystemExit) as exc_info:
        cdc.main([])

    assert exc_info.value.code == 2
    assert "BLOCKED" in capsys.readouterr().out


def test_main_dry_run_makes_no_copper_calls(monkeypatch):
    _configure_env(monkeypatch)
    plan = [{
        "company_name": "Acme Co (dup)", "copper_id": "100", "existing_tags": [],
        "decision": {"action": "close", "detail_text": "Duplicate of Acme Co (Copper #200)"},
    }]
    _stub_fetch_plan(monkeypatch, plan)

    def fail_close(*a, **k):
        raise AssertionError("must not write to Copper during a dry run")

    monkeypatch.setattr(copper_writer, "close_duplicate_in_copper", fail_close)

    exit_code = cdc.main([])
    assert exit_code == 0


def test_main_commit_closes_only_the_close_rows(monkeypatch):
    _configure_env(monkeypatch)
    plan = [
        {"company_name": "Acme Co (dup)", "copper_id": "100", "existing_tags": [],
         "decision": {"action": "close", "detail_text": "Duplicate of Acme Co (Copper #200)"}},
        {"company_name": "AlreadyDone", "copper_id": "101", "existing_tags": [],
         "decision": {"action": "skip", "reason": "already Unqualified in Copper"}},
        {"company_name": "HiddenLeadCo", "copper_id": "102", "existing_tags": [],
         "decision": {"action": "needs_review", "reason": "surviving record Copper #999 is missing"}},
    ]
    _stub_fetch_plan(monkeypatch, plan)

    calls = []
    monkeypatch.setattr(
        copper_writer, "close_duplicate_in_copper",
        lambda copper_id, existing_tags, detail_text: calls.append(copper_id) or "row-id",
    )

    exit_code = cdc.main(["--commit"])

    assert exit_code == 0
    assert calls == ["100"]


def test_main_commit_with_nothing_to_close_makes_no_calls(monkeypatch):
    _configure_env(monkeypatch)
    plan = [{"company_name": "AlreadyDone", "copper_id": "101", "existing_tags": [],
             "decision": {"action": "skip", "reason": "already Unqualified in Copper"}}]
    _stub_fetch_plan(monkeypatch, plan)

    def fail_close(*a, **k):
        raise AssertionError("nothing to close -- must not call Copper")

    monkeypatch.setattr(copper_writer, "close_duplicate_in_copper", fail_close)

    exit_code = cdc.main(["--commit"])
    assert exit_code == 0


def test_main_commit_returns_nonzero_when_a_write_fails(monkeypatch):
    _configure_env(monkeypatch)
    plan = [{"company_name": "Acme Co (dup)", "copper_id": "100", "existing_tags": [],
             "decision": {"action": "close", "detail_text": "Duplicate of Acme Co (Copper #200)"}}]
    _stub_fetch_plan(monkeypatch, plan)

    def fail_close(*a, **k):
        raise RuntimeError("copper 500")

    monkeypatch.setattr(copper_writer, "close_duplicate_in_copper", fail_close)

    exit_code = cdc.main(["--commit"])
    assert exit_code == 1
