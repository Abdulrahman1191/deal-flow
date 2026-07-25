"""
Tests for scripts/close_stale_copper.py.

plan_cleanup() is a pure function (mirrors the reconcile_copper.py test
pattern -- no live DB, no live Copper). apply_cleanup()/main() are tested
with execute_copper_request mocked, confirming --commit issues the expected
Copper calls and dry-run issues none, and that one failure never aborts the
rest -- mirrors the mocking pattern in test_copper_writebacks.py.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import close_stale_copper as csc  # noqa: E402
import reconcile_copper as rc  # noqa: E402


def copper_lead(cid, company=None, name=None):
    d = {"id": cid}
    if company is not None:
        d["company_name"] = company
    if name is not None:
        d["name"] = name
    return d


def app_lead(id_, company, copper_id=None, status="pending",
             archive_event_type=None, archive_reason=None):
    return rc.AppLead(
        id=id_, company_name=company, copper_id=copper_id, raw_copper_id=None,
        status=status, archive_event_type=archive_event_type, archive_reason=archive_reason,
    )


# --- plan_cleanup ------------------------------------------------------------

def test_plan_identifies_stale_archived_lead():
    lead = app_lead("1", "DupCo", copper_id="100", status="archived",
                     archive_event_type="archived", archive_reason="duplicate")
    plan = csc.plan_cleanup([copper_lead("100", company="DupCo")], [lead])

    assert plan["stale"] == [
        {"company_name": "DupCo", "copper_id": "100", "reason": "archived", "sub_reason": "duplicate"}
    ]
    assert plan["test_records"] == []


def test_plan_ignores_active_leads():
    lead = app_lead("1", "ActiveCo", copper_id="100", status="pending")
    plan = csc.plan_cleanup([copper_lead("100", company="ActiveCo")], [lead])
    assert plan["stale"] == []
    assert plan["test_records"] == []


def test_plan_ignores_leads_not_yet_synced_to_the_app():
    """not_synced / approved / status_mismatch aren't "archived" -- out of
    scope for this cleanup (only the archived-in-app drift is stale here)."""
    plan = csc.plan_cleanup([copper_lead("999", company="NewCo")], [])
    assert plan["stale"] == []


def test_plan_identifies_test_record_by_company_prefix():
    plan = csc.plan_cleanup([copper_lead("200", company="ZZZ-RAED-AUTOSYNC-TEST Co")], [])
    assert plan["test_records"] == [{"company_name": "ZZZ-RAED-AUTOSYNC-TEST Co", "copper_id": "200"}]
    assert plan["stale"] == []


def test_plan_identifies_test_record_by_name_prefix_when_no_company():
    plan = csc.plan_cleanup([copper_lead("201", name="ZZZ-RAED-SYNC-TEST")], [])
    assert plan["test_records"] == [{"company_name": "ZZZ-RAED-SYNC-TEST", "copper_id": "201"}]


def test_plan_test_record_that_is_also_archived_only_appears_as_test_record():
    """A ZZZ-RAED-* lead that's ALSO archived in the app should only be
    deleted, not also set Unqualified in the same run."""
    lead = app_lead("1", "ZZZ-RAED-DUP Co", copper_id="300", status="archived",
                     archive_event_type="archived", archive_reason="duplicate")
    plan = csc.plan_cleanup([copper_lead("300", company="ZZZ-RAED-DUP Co")], [lead])

    assert plan["stale"] == []
    assert plan["test_records"] == [{"company_name": "ZZZ-RAED-DUP Co", "copper_id": "300"}]


def test_plan_mixed_set():
    stale_lead = app_lead("1", "DupCo", copper_id="100", status="archived",
                           archive_event_type="archived", archive_reason="duplicate")
    active_lead = app_lead("2", "ActiveCo", copper_id="101", status="pending")
    copper_leads = [
        copper_lead("100", company="DupCo"),
        copper_lead("101", company="ActiveCo"),
        copper_lead("200", company="ZZZ-RAED-AUTOSYNC-TEST"),
    ]
    plan = csc.plan_cleanup(copper_leads, [stale_lead, active_lead])

    assert [r["copper_id"] for r in plan["stale"]] == ["100"]
    assert [r["copper_id"] for r in plan["test_records"]] == ["200"]


# --- apply_cleanup ----------------------------------------------------------

def test_apply_cleanup_sets_unqualified_for_stale(monkeypatch):
    calls = []
    monkeypatch.setattr(csc, "execute_copper_request",
                         lambda endpoint, method, body: calls.append((endpoint, method, body)))
    monkeypatch.setattr(csc.settings, "copper_unqualified_status_id", 999)

    plan = {"stale": [{"company_name": "DupCo", "copper_id": "100", "reason": "archived", "sub_reason": "duplicate"}],
            "test_records": []}
    result = csc.apply_cleanup(plan)

    assert calls == [("/leads/100", "PUT", {"status_id": 999})]
    assert result["stale_ok"] == ["100"]
    assert result["stale_failed"] == []


def test_apply_cleanup_deletes_test_records(monkeypatch):
    calls = []
    monkeypatch.setattr(csc, "execute_copper_request",
                         lambda endpoint, method, body: calls.append((endpoint, method, body)))

    plan = {"stale": [], "test_records": [{"company_name": "ZZZ-RAED-X", "copper_id": "200"}]}
    result = csc.apply_cleanup(plan)

    assert calls == [("/leads/200", "DELETE", {})]
    assert result["test_deleted"] == ["200"]


def test_apply_cleanup_falls_back_to_unqualified_when_delete_fails(monkeypatch):
    monkeypatch.setattr(csc.settings, "copper_unqualified_status_id", 999)
    calls = []

    def fake_execute(endpoint, method, body):
        calls.append((endpoint, method, body))
        if method == "DELETE":
            raise RuntimeError("delete not allowed")
        return {}

    monkeypatch.setattr(csc, "execute_copper_request", fake_execute)

    plan = {"stale": [], "test_records": [{"company_name": "ZZZ-RAED-X", "copper_id": "200"}]}
    result = csc.apply_cleanup(plan)

    assert calls == [("/leads/200", "DELETE", {}), ("/leads/200", "PUT", {"status_id": 999})]
    assert result["test_unqualified"] == ["200"]
    assert result["test_deleted"] == []
    assert result["test_failed"] == []


def test_apply_cleanup_records_failure_when_both_delete_and_fallback_fail(monkeypatch):
    def fake_execute(endpoint, method, body):
        raise RuntimeError(f"copper down ({method})")

    monkeypatch.setattr(csc, "execute_copper_request", fake_execute)

    plan = {"stale": [], "test_records": [{"company_name": "ZZZ-RAED-X", "copper_id": "200"}]}
    result = csc.apply_cleanup(plan)

    assert result["test_failed"] == ["200"]
    assert result["test_deleted"] == []
    assert result["test_unqualified"] == []


def test_apply_cleanup_one_failure_does_not_abort_the_rest(monkeypatch):
    calls = []

    def fake_execute(endpoint, method, body):
        calls.append(endpoint)
        if endpoint == "/leads/100":
            raise RuntimeError("copper 500")
        return {}

    monkeypatch.setattr(csc, "execute_copper_request", fake_execute)

    plan = {
        "stale": [
            {"company_name": "A", "copper_id": "100", "reason": "archived"},
            {"company_name": "B", "copper_id": "101", "reason": "archived"},
        ],
        "test_records": [],
    }
    result = csc.apply_cleanup(plan)

    assert calls == ["/leads/100", "/leads/101"]
    assert result["stale_failed"] == ["100"]
    assert result["stale_ok"] == ["101"]


# --- main(): env guard, dry-run vs --commit ----------------------------------

def _configure_env(monkeypatch, copper_user_id=1):
    monkeypatch.setattr(csc.settings, "copper_api_key", "k")
    monkeypatch.setattr(csc.settings, "copper_user_email", "u@raed.vc")
    monkeypatch.setattr(csc.settings, "copper_user_id", copper_user_id)
    monkeypatch.setattr(csc.settings, "copper_open_status_id", 5)
    monkeypatch.setattr(csc.settings, "copper_unqualified_status_id", 999)
    monkeypatch.setattr(csc.settings, "owner_email", "owner@raed.vc")


def _stub_fetch_plan(monkeypatch, plan):
    async def fake_fetch_plan(copper_user_id, owner_email):
        return plan

    monkeypatch.setattr(csc, "_fetch_plan", fake_fetch_plan)


def test_main_refuses_without_copper_env(monkeypatch, capsys):
    monkeypatch.setattr(csc.settings, "copper_api_key", "")

    with pytest.raises(SystemExit) as exc_info:
        csc.main([])

    assert exc_info.value.code == 2
    assert "BLOCKED" in capsys.readouterr().out


def test_main_refuses_without_copper_user_id_unless_flag_passed(monkeypatch, capsys):
    _configure_env(monkeypatch, copper_user_id=0)

    with pytest.raises(SystemExit) as exc_info:
        csc.main([])
    assert exc_info.value.code == 2
    assert "COPPER_USER_ID" in capsys.readouterr().out


def test_main_dry_run_issues_no_copper_calls(monkeypatch):
    _configure_env(monkeypatch)
    plan = {
        "stale": [{"company_name": "DupCo", "copper_id": "100", "reason": "archived", "sub_reason": "duplicate"}],
        "test_records": [{"company_name": "ZZZ-RAED-X", "copper_id": "200"}],
    }
    _stub_fetch_plan(monkeypatch, plan)

    def fail_execute(*a, **k):
        raise AssertionError("must not write to Copper during a dry run")

    monkeypatch.setattr(csc, "execute_copper_request", fail_execute)

    exit_code = csc.main([])
    assert exit_code == 0


def test_main_commit_issues_expected_copper_calls(monkeypatch):
    _configure_env(monkeypatch)
    plan = {
        "stale": [{"company_name": "DupCo", "copper_id": "100", "reason": "archived", "sub_reason": "duplicate"}],
        "test_records": [{"company_name": "ZZZ-RAED-AUTOSYNC-TEST", "copper_id": "200"}],
    }
    _stub_fetch_plan(monkeypatch, plan)

    calls = []

    def fake_execute(endpoint, method, body):
        calls.append((endpoint, method, body))
        return {}

    monkeypatch.setattr(csc, "execute_copper_request", fake_execute)

    exit_code = csc.main(["--commit"])

    assert exit_code == 0
    assert ("/leads/100", "PUT", {"status_id": 999}) in calls
    assert ("/leads/200", "DELETE", {}) in calls


def test_main_commit_returns_nonzero_when_a_write_fails(monkeypatch):
    _configure_env(monkeypatch)
    plan = {"stale": [{"company_name": "DupCo", "copper_id": "100", "reason": "archived"}], "test_records": []}
    _stub_fetch_plan(monkeypatch, plan)

    def fail_execute(*a, **k):
        raise RuntimeError("copper 500")

    monkeypatch.setattr(csc, "execute_copper_request", fail_execute)

    exit_code = csc.main(["--commit"])
    assert exit_code == 1


def test_main_commit_with_nothing_to_do_makes_no_calls(monkeypatch):
    _configure_env(monkeypatch)
    _stub_fetch_plan(monkeypatch, {"stale": [], "test_records": []})

    def fail_execute(*a, **k):
        raise AssertionError("nothing to do -- must not call Copper")

    monkeypatch.setattr(csc, "execute_copper_request", fail_execute)

    exit_code = csc.main(["--commit"])
    assert exit_code == 0
