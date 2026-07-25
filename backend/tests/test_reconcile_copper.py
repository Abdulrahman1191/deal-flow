"""
Tests for scripts/reconcile_copper.py's reconciliation helpers.

Pure unit tests against a synthetic Copper-open set + app leads (AppLead) --
no live DB, no live Copper calls, mirrors the pattern in
test_data_quality_report.py.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import reconcile_copper as rc  # noqa: E402


def copper_lead(cid, company="Acme"):
    return {"id": cid, "company_name": company}


def app_lead(id_, company, copper_id=None, raw_copper_id=None, status="pending",
             archive_event_type=None, archive_reason=None):
    return rc.AppLead(
        id=id_, company_name=company, copper_id=copper_id, raw_copper_id=raw_copper_id,
        status=status, archive_event_type=archive_event_type, archive_reason=archive_reason,
    )


# --- classify_archive_reason --------------------------------------------------

def test_classify_archive_reason_no_reply_event():
    assert rc.classify_archive_reason("archived_no_reply", None) == "no_reply"


def test_classify_archive_reason_duplicate():
    assert rc.classify_archive_reason("archived", "duplicate") == "duplicate"


def test_classify_archive_reason_rejection():
    assert rc.classify_archive_reason("archived", "rejection") == "rejection"


def test_classify_archive_reason_copper_reconcile_prefix():
    reason = "copper_reconcile: no longer open-assigned to user"
    assert rc.classify_archive_reason("archived", reason) == "copper_reconcile"


def test_classify_archive_reason_deleted_in_copper():
    assert rc.classify_archive_reason("archived", "deleted_in_copper") == "deleted_in_copper"


def test_classify_archive_reason_missing_reason():
    assert rc.classify_archive_reason("archived", None) == "unspecified"


# --- index_leads ---------------------------------------------------------------

def test_index_leads_splits_by_copper_id_presence():
    with_id = app_lead("1", "A", copper_id="100")
    nulled = app_lead("2", "B", copper_id=None, raw_copper_id="200")
    neither = app_lead("3", "C", copper_id=None, raw_copper_id=None)

    by_copper_id, by_raw_id = rc.index_leads([with_id, nulled, neither])

    assert by_copper_id == {"100": with_id}
    assert by_raw_id == {"200": nulled}


# --- categorize_copper_open ----------------------------------------------------

def test_categorize_copper_open_active_status_is_on_dashboard_not_a_gap():
    lead = app_lead("1", "Acme", copper_id="100", status="pending")
    by_copper_id, by_raw_id = rc.index_leads([lead])

    rows, counts = rc.categorize_copper_open([copper_lead("100", "Acme")], by_copper_id, by_raw_id)

    assert rows == []
    assert counts["on_dashboard"] == 1


def test_categorize_copper_open_approved():
    lead = app_lead("1", "Acme", copper_id="100", status="approved")
    by_copper_id, by_raw_id = rc.index_leads([lead])

    rows, counts = rc.categorize_copper_open([copper_lead("100", "Acme")], by_copper_id, by_raw_id)

    assert rows == [{"company_name": "Acme", "copper_id": "100", "reason": "approved"}]
    assert counts["approved"] == 1


def test_categorize_copper_open_archived_with_sub_reason():
    lead = app_lead("1", "Acme", copper_id="100", status="archived",
                     archive_event_type="archived", archive_reason="duplicate")
    by_copper_id, by_raw_id = rc.index_leads([lead])

    rows, counts = rc.categorize_copper_open([copper_lead("100", "Acme")], by_copper_id, by_raw_id)

    assert rows == [{"company_name": "Acme", "copper_id": "100", "reason": "archived", "sub_reason": "duplicate"}]
    assert counts["archived"] == 1


def test_categorize_copper_open_not_synced():
    rows, counts = rc.categorize_copper_open([copper_lead("999", "NewCo")], {}, {})

    assert rows == [{"company_name": "NewCo", "copper_id": "999", "reason": "not_synced"}]
    assert counts["not_synced"] == 1


def test_categorize_copper_open_converted_or_sent_via_raw_id_fallback():
    lead = app_lead("1", "Acme", copper_id=None, raw_copper_id="100", status="archived")
    by_copper_id, by_raw_id = rc.index_leads([lead])

    rows, counts = rc.categorize_copper_open([copper_lead("100", "Acme")], by_copper_id, by_raw_id)

    assert rows == [{"company_name": "Acme", "copper_id": "100", "reason": "converted_or_sent"}]
    assert counts["converted_or_sent"] == 1


def test_categorize_copper_open_status_mismatch_for_unknown_status():
    lead = app_lead("1", "Acme", copper_id="100", status="weird_future_status")
    by_copper_id, by_raw_id = rc.index_leads([lead])

    rows, counts = rc.categorize_copper_open([copper_lead("100", "Acme")], by_copper_id, by_raw_id)

    assert rows == [{"company_name": "Acme", "copper_id": "100", "reason": "status_mismatch"}]
    assert counts["status_mismatch"] == 1


# --- categorize_dashboard_only ---------------------------------------------------

def test_categorize_dashboard_only_matched_lead_is_not_a_gap():
    lead = app_lead("1", "Acme", copper_id="100", status="pending")
    rows, counts = rc.categorize_dashboard_only([lead], copper_open_ids={"100"})
    assert rows == []
    assert counts == {}


def test_categorize_dashboard_only_no_copper_id():
    lead = app_lead("1", "Acme", copper_id=None, status="pending")
    rows, counts = rc.categorize_dashboard_only([lead], copper_open_ids=set())
    assert rows == [{"company_name": "Acme", "copper_id": None, "reason": "no_copper_id"}]
    assert counts["no_copper_id"] == 1


def test_categorize_dashboard_only_not_open_in_copper():
    lead = app_lead("1", "Acme", copper_id="100", status="pending")
    rows, counts = rc.categorize_dashboard_only([lead], copper_open_ids={"999"})
    assert rows == [{"company_name": "Acme", "copper_id": "100", "reason": "not_open_in_copper"}]
    assert counts["not_open_in_copper"] == 1


# --- build_reconciliation: totals reconcile end-to-end --------------------------

def test_build_reconciliation_totals_reconcile():
    """8 duplicates, 6 rejections, 3 approved, 1 not-synced -- 18 total gap,
    matching the issue's live numbers (177 copper_open, 159 dashboard_active)."""
    copper_leads = []
    app_leads = []

    # 159 matched, active leads -- on both sets, no gap.
    for i in range(159):
        cid = f"active-{i}"
        copper_leads.append(copper_lead(cid, f"ActiveCo{i}"))
        app_leads.append(app_lead(f"lead-{i}", f"ActiveCo{i}", copper_id=cid, status="pending"))

    # 8 duplicates (archived locally, still open in Copper).
    for i in range(8):
        cid = f"dup-{i}"
        copper_leads.append(copper_lead(cid, f"DupCo{i}"))
        app_leads.append(app_lead(f"dup-lead-{i}", f"DupCo{i}", copper_id=cid, status="archived",
                                   archive_event_type="archived", archive_reason="duplicate"))

    # 6 rejections.
    for i in range(6):
        cid = f"rej-{i}"
        copper_leads.append(copper_lead(cid, f"RejCo{i}"))
        app_leads.append(app_lead(f"rej-lead-{i}", f"RejCo{i}", copper_id=cid, status="archived",
                                   archive_event_type="archived", archive_reason="rejection"))

    # 3 approved (send queue).
    for i in range(3):
        cid = f"appr-{i}"
        copper_leads.append(copper_lead(cid, f"ApprCo{i}"))
        app_leads.append(app_lead(f"appr-lead-{i}", f"ApprCo{i}", copper_id=cid, status="approved"))

    # 1 not-synced -- Copper has it, app doesn't (recent, not yet synced).
    copper_leads.append(copper_lead("new-1", "BrandNewCo"))

    assert len(copper_leads) == 177

    result = rc.build_reconciliation(copper_leads, app_leads)

    assert result["copper_open"] == 177
    assert result["dashboard_active"] == 159
    assert result["on_dashboard_and_copper_open"] == 159

    mf_counts = result["missing_from_dashboard"]["counts"]
    assert mf_counts == {"archived": 14, "approved": 3, "not_synced": 1}
    assert sum(mf_counts.values()) == 18
    assert len(result["missing_from_dashboard"]["rows"]) == 18

    sub_reasons = [r.get("sub_reason") for r in result["missing_from_dashboard"]["rows"] if r["reason"] == "archived"]
    assert sub_reasons.count("duplicate") == 8
    assert sub_reasons.count("rejection") == 6

    # Reverse direction: no drift in this scenario.
    assert result["dashboard_not_in_copper"]["rows"] == []

    # The two totals reconcile down to the individual lead.
    assert result["copper_open"] == result["on_dashboard_and_copper_open"] + sum(mf_counts.values())
    assert result["dashboard_active"] == (
        result["on_dashboard_and_copper_open"] + len(result["dashboard_not_in_copper"]["rows"])
    )


def test_build_reconciliation_reverse_drift_not_open_in_copper():
    """A lead active on the dashboard whose Copper status changed externally
    (no longer open) shows up in dashboard_not_in_copper, not silently dropped."""
    copper_leads = [copper_lead("100", "Acme")]
    app_leads = [
        app_lead("1", "Acme", copper_id="100", status="pending"),
        app_lead("2", "Drifted", copper_id="200", status="pending"),
    ]

    result = rc.build_reconciliation(copper_leads, app_leads)

    assert result["copper_open"] == 1
    assert result["dashboard_active"] == 2
    assert result["on_dashboard_and_copper_open"] == 1
    assert result["dashboard_not_in_copper"]["rows"] == [
        {"company_name": "Drifted", "copper_id": "200", "reason": "not_open_in_copper"}
    ]
