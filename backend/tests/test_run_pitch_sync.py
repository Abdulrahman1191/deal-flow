"""
Tests for scripts/run_pitch_sync.py's report-formatting helpers.

The Drive/network side (service-account auth, folder listing) isn't unit
tested here -- that's what the script's --check flag is for, run manually
against the real service account. These tests cover the pure string
formatting so the CLI's report can't silently regress.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run_pitch_sync as rps  # noqa: E402


def test_format_check_report_lists_files_within_limit():
    files = [{"id": "1", "name": "Acme.pdf"}, {"id": "2", "name": "Hadawi.pdf"}]
    report = rps.format_check_report("sa@project.iam.gserviceaccount.com", "folder123", files)

    assert "sa@project.iam.gserviceaccount.com" in report
    assert "folder123" in report
    assert "2 file(s) visible" in report
    assert "- Acme.pdf" in report
    assert "- Hadawi.pdf" in report
    assert "more" not in report


def test_format_check_report_truncates_long_file_lists():
    files = [{"id": str(i), "name": f"Deck{i}.pdf"} for i in range(15)]
    report = rps.format_check_report("sa@project.iam.gserviceaccount.com", "folder123", files, max_names=10)

    assert "15 file(s) visible" in report
    assert "Deck9.pdf" in report
    assert "Deck10.pdf" not in report
    assert "... and 5 more" in report


def test_format_check_report_empty_folder():
    report = rps.format_check_report("sa@project.iam.gserviceaccount.com", "folder123", [])
    assert "0 file(s) visible" in report


def test_format_sync_summary_reports_all_fields():
    result = {
        "drive_files": 5,
        "matched": 3,
        "unmatched": 2,
        "reassessments_queued": 1,
    }
    summary = rps.format_sync_summary(result)

    assert "Drive files seen:      5" in summary
    assert "Leads matched:         3" in summary
    assert "Decks newly attached:  3" in summary
    assert "Reassessments queued:  1" in summary
    assert "Unmatched files:       2" in summary


def test_main_exits_nonzero_without_traceback_when_credential_unset(monkeypatch, capsys):
    monkeypatch.setattr(rps.settings, "google_service_account_json", "")
    exit_code = rps.main([])

    assert exit_code != 0
    assert "GOOGLE_SERVICE_ACCOUNT_JSON is not set" in capsys.readouterr().out


def test_run_check_reports_invalid_json(monkeypatch, capsys):
    monkeypatch.setattr(rps.settings, "google_service_account_json", "not-json")
    exit_code = rps.run_check()

    assert exit_code != 0
    assert "not valid JSON" in capsys.readouterr().out


def test_run_check_reports_wrong_key_type(monkeypatch, capsys):
    monkeypatch.setattr(
        rps.settings, "google_service_account_json", '{"type": "authorized_user"}'
    )
    exit_code = rps.run_check()

    assert exit_code != 0
    assert "wrong key type" in capsys.readouterr().out


def test_run_check_reports_drive_error_cause(monkeypatch, capsys):
    monkeypatch.setattr(
        rps.settings,
        "google_service_account_json",
        '{"type": "service_account", "client_email": "sa@project.iam.gserviceaccount.com"}',
    )
    monkeypatch.setattr(rps.settings, "drive_pitch_deck_folder_id", "folder123")
    monkeypatch.setattr(rps, "_drive_service", lambda: object())

    def _boom(service, folder_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(rps, "_list_pdfs_in_folder", _boom)
    exit_code = rps.run_check()

    assert exit_code != 0
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "RuntimeError: boom" in out
