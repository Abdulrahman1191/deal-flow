"""
Tests for scripts/reconcile_ownership.py (issue #114).

find_mismatches/resolve_assignee_email/filter_by_owner/filter_by_pair are
pure functions over synthetic AppLead rows + a Copper index -- no live DB, no
live Copper, mirrors the pattern in test_reconcile_copper.py. apply_fix()/
main() are exercised against a queued-result AsyncSession fake, mirroring
test_sync_copper.py / test_backfill_awaiting_deck.py -- no live DB needed.
"""
from __future__ import annotations
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import reconcile_ownership as ro  # noqa: E402

ABDULRAHMAN_ID = 1181364
UDAY_ID = 1150537
USER_MAP = {ABDULRAHMAN_ID: "abdulrahman@raed.vc", UDAY_ID: "uday@raed.vc"}


def app_lead(id_, company, copper_id, owner_email):
    return ro.AppLead(id=id_, company_name=company, copper_id=copper_id, owner_email=owner_email)


def copper_lead(cid, assignee_id):
    return {"id": cid, "assignee_id": assignee_id}


# --- resolve_assignee_email ---------------------------------------------------

def test_resolve_assignee_email_known_user():
    assert ro.resolve_assignee_email(UDAY_ID, USER_MAP) == "uday@raed.vc"


def test_resolve_assignee_email_falsy_is_unassigned():
    assert ro.resolve_assignee_email(None, USER_MAP) == "unassigned"
    assert ro.resolve_assignee_email(0, USER_MAP) == "unassigned"


def test_resolve_assignee_email_unknown_id():
    assert ro.resolve_assignee_email(999999, USER_MAP) == "unknown(999999)"


# --- find_mismatches -----------------------------------------------------------

def test_find_mismatches_reports_a_lead_reassigned_in_copper():
    """The core issue #114 scenario: Copper reassigned copper_id=100 from
    abdulrahman to uday, but the app row still has owner_email=abdulrahman."""
    lead = app_lead("1", "Acme", copper_id="100", owner_email="abdulrahman@raed.vc")
    copper_index = {"100": copper_lead("100", UDAY_ID)}

    rows = ro.find_mismatches([lead], copper_index, USER_MAP)

    assert len(rows) == 1
    m = rows[0]
    assert m.lead_id == "1"
    assert m.company_name == "Acme"
    assert m.copper_id == "100"
    assert m.dealflow_owner == "abdulrahman@raed.vc"
    assert m.copper_assignee == "uday@raed.vc"


def test_find_mismatches_no_mismatch_when_owners_match():
    lead = app_lead("1", "Acme", copper_id="100", owner_email="uday@raed.vc")
    copper_index = {"100": copper_lead("100", UDAY_ID)}
    assert ro.find_mismatches([lead], copper_index, USER_MAP) == []


def test_find_mismatches_case_insensitive_no_false_positive():
    lead = app_lead("1", "Acme", copper_id="100", owner_email="Uday@Raed.VC")
    copper_index = {"100": copper_lead("100", UDAY_ID)}
    assert ro.find_mismatches([lead], copper_index, USER_MAP) == []


def test_find_mismatches_reports_reverse_direction():
    """Same drift, opposite direction: app says uday, Copper now says abdulrahman."""
    lead = app_lead("1", "Beta", copper_id="200", owner_email="uday@raed.vc")
    copper_index = {"200": copper_lead("200", ABDULRAHMAN_ID)}

    rows = ro.find_mismatches([lead], copper_index, USER_MAP)
    assert rows[0].dealflow_owner == "uday@raed.vc"
    assert rows[0].copper_assignee == "abdulrahman@raed.vc"


def test_find_mismatches_unassigned_in_copper():
    lead = app_lead("1", "Gamma", copper_id="300", owner_email="abdulrahman@raed.vc")
    copper_index = {"300": copper_lead("300", None)}
    rows = ro.find_mismatches([lead], copper_index, USER_MAP)
    assert rows[0].copper_assignee == "unassigned"


def test_find_mismatches_assignee_outside_known_users():
    lead = app_lead("1", "Delta", copper_id="400", owner_email="abdulrahman@raed.vc")
    copper_index = {"400": copper_lead("400", 777777)}
    rows = ro.find_mismatches([lead], copper_index, USER_MAP)
    assert rows[0].copper_assignee == "unknown(777777)"


def test_find_mismatches_skips_lead_not_found_in_copper():
    """copper_id no longer present in the firm-wide Copper fetch (e.g. deleted
    in Copper) -- nothing current to compare against, so it's not reported."""
    lead = app_lead("1", "Zeta", copper_id="999", owner_email="abdulrahman@raed.vc")
    assert ro.find_mismatches([lead], {}, USER_MAP) == []


def test_find_mismatches_idempotent_after_fix():
    """Simulates applying --fix (setting owner_email to the resolved Copper
    assignee) and confirms a rerun reports no further mismatches."""
    lead = app_lead("1", "Acme", copper_id="100", owner_email="abdulrahman@raed.vc")
    copper_index = {"100": copper_lead("100", UDAY_ID)}

    rows = ro.find_mismatches([lead], copper_index, USER_MAP)
    assert len(rows) == 1

    fixed_lead = app_lead("1", "Acme", copper_id="100", owner_email=rows[0].copper_assignee)
    assert ro.find_mismatches([fixed_lead], copper_index, USER_MAP) == []


# --- filter_by_owner / filter_by_pair ------------------------------------------

def test_filter_by_owner_matches_either_side():
    away = ro.Mismatch("1", "Away", "100", "abdulrahman@raed.vc", "uday@raed.vc")
    toward = ro.Mismatch("2", "Toward", "200", "waleed@raed.vc", "abdulrahman@raed.vc")
    unrelated = ro.Mismatch("3", "Unrelated", "300", "waleed@raed.vc", "yomna@raed.vc")

    result = ro.filter_by_owner([away, toward, unrelated], "abdulrahman@raed.vc")
    assert result == [away, toward]


def test_filter_by_pair_both_directions():
    matching = ro.Mismatch("1", "A", "100", "abdulrahman@raed.vc", "uday@raed.vc")
    reverse = ro.Mismatch("2", "B", "200", "uday@raed.vc", "abdulrahman@raed.vc")
    outside = ro.Mismatch("3", "C", "300", "abdulrahman@raed.vc", "waleed@raed.vc")

    result = ro.filter_by_pair([matching, reverse, outside], ("abdulrahman@raed.vc", "uday@raed.vc"))
    assert result == [matching, reverse]


# --- Mismatch.fixable -----------------------------------------------------------

def test_fixable_true_for_known_user():
    m = ro.Mismatch("1", "A", "100", "abdulrahman@raed.vc", "uday@raed.vc")
    assert m.fixable is True


def test_fixable_false_for_unassigned():
    m = ro.Mismatch("1", "A", "100", "abdulrahman@raed.vc", "unassigned")
    assert m.fixable is False


def test_fixable_false_for_unknown():
    m = ro.Mismatch("1", "A", "100", "abdulrahman@raed.vc", "unknown(777)")
    assert m.fixable is False


# --- group_counts ----------------------------------------------------------------

def test_group_counts_tallies_per_pair():
    rows = [
        ro.Mismatch("1", "A", "100", "abdulrahman@raed.vc", "uday@raed.vc"),
        ro.Mismatch("2", "B", "200", "abdulrahman@raed.vc", "uday@raed.vc"),
        ro.Mismatch("3", "C", "300", "waleed@raed.vc", "yomna@raed.vc"),
    ]
    counts = ro.group_counts(rows)
    assert counts[("abdulrahman@raed.vc", "uday@raed.vc")] == 2
    assert counts[("waleed@raed.vc", "yomna@raed.vc")] == 1


# --- apply_fix: queued-result AsyncSession fake --------------------------------

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    async def commit(self):
        self.committed += 1


def _db_lead(lead_id, owner_email, company_name="Acme"):
    return SimpleNamespace(id=lead_id, company_name=company_name, owner_email=owner_email)


def test_apply_fix_updates_owner_email_and_commits():
    lead_id = uuid.uuid4()
    db_lead = _db_lead(lead_id, "abdulrahman@raed.vc")
    mismatch = ro.Mismatch(str(lead_id), "Acme", "100", "abdulrahman@raed.vc", "uday@raed.vc")
    session = _FakeSession([[db_lead]])

    fixed = asyncio.run(ro.apply_fix(session, [mismatch]))

    assert fixed == [mismatch]
    assert db_lead.owner_email == "uday@raed.vc"
    assert session.committed == 1


def test_apply_fix_skips_unfixable_mismatches_and_makes_no_writes():
    mismatch = ro.Mismatch(str(uuid.uuid4()), "Acme", "100", "abdulrahman@raed.vc", "unassigned")
    session = _FakeSession([])

    fixed = asyncio.run(ro.apply_fix(session, [mismatch]))

    assert fixed == []
    assert session.committed == 0


def test_apply_fix_only_commits_fixable_subset():
    fixable_id = uuid.uuid4()
    db_lead = _db_lead(fixable_id, "abdulrahman@raed.vc")
    fixable = ro.Mismatch(str(fixable_id), "Acme", "100", "abdulrahman@raed.vc", "uday@raed.vc")
    unfixable = ro.Mismatch(str(uuid.uuid4()), "Beta", "200", "abdulrahman@raed.vc", "unassigned")
    session = _FakeSession([[db_lead]])

    fixed = asyncio.run(ro.apply_fix(session, [fixable, unfixable]))

    assert fixed == [fixable]
    assert db_lead.owner_email == "uday@raed.vc"
    assert session.committed == 1


# --- main(): report vs --fix ---------------------------------------------------

def _stub_sessions(monkeypatch, *sessions):
    it = iter(sessions)
    monkeypatch.setattr(ro, "AsyncSessionLocal", lambda: next(it))


def test_main_report_only_lists_mismatch_and_makes_no_writes(monkeypatch, capsys):
    lead_id = uuid.uuid4()
    db_lead = _db_lead(lead_id, "abdulrahman@raed.vc", "Acme")
    monkeypatch.setattr(ro, "fetch_all_leads", lambda: [copper_lead("100", UDAY_ID)])
    db_lead.copper_id = "100"
    session = _FakeSession([[db_lead], list(USER_MAP.items())])
    _stub_sessions(monkeypatch, session)

    exit_code = ro.main([])

    assert exit_code == 0
    assert session.committed == 0
    out = capsys.readouterr().out
    assert "Acme" in out
    assert "abdulrahman@raed.vc" in out
    assert "uday@raed.vc" in out
    assert "Total mismatches: 1" in out


def test_main_fix_corrects_the_mismatch(monkeypatch, capsys):
    lead_id = uuid.uuid4()
    report_lead = _db_lead(lead_id, "abdulrahman@raed.vc", "Acme")
    report_lead.copper_id = "100"
    fix_lead = _db_lead(lead_id, "abdulrahman@raed.vc", "Acme")

    monkeypatch.setattr(ro, "fetch_all_leads", lambda: [copper_lead("100", UDAY_ID)])
    report_session = _FakeSession([[report_lead], list(USER_MAP.items())])
    fix_session = _FakeSession([[fix_lead]])
    _stub_sessions(monkeypatch, report_session, fix_session)

    exit_code = ro.main(["--fix"])

    assert exit_code == 0
    assert fix_lead.owner_email == "uday@raed.vc"
    assert fix_session.committed == 1
    out = capsys.readouterr().out
    assert "--fix applied: corrected 1 lead(s)." in out
