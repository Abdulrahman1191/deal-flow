"""
Tests for scripts/reextract_pitch_decks.py (issue #97).

plan_reextract() is a pure function over already-fetched candidate rows
(mirrors test_backfill_awaiting_deck.py's pattern -- no live DB, no live
Drive). _fetch_candidates()/apply_reextract()/main() are exercised with a
fake AsyncSessionLocal and a fake Drive service+downloader, confirming: the
DB filter targets leads with a Drive deck attached but no usable text yet,
--commit re-extracts and writes/queues while dry-run doesn't, a lead that
still yields nothing is left alone, and one lead's failure doesn't abort the
rest of the batch.
"""
from __future__ import annotations
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import reextract_pitch_decks as rpd  # noqa: E402


def _lead(owner_email, company_name="Acme", drive_id="drive-1", filename="Acme.pdf", pitch_deck_text=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email=owner_email,
        company_name=company_name,
        pitch_deck_drive_id=drive_id,
        pitch_deck_filename=filename,
        pitch_deck_text=pitch_deck_text,
        pitch_deck_ingested_at=None,
    )


# --- plan_reextract (pure) ---------------------------------------------------


def test_plan_lists_all_candidates():
    leads = [
        _lead("alice@raed.vc", "Co A", drive_id="d1"),
        _lead("bob@raed.vc", "Co B", drive_id="d2"),
    ]
    plan = rpd.plan_reextract(leads)

    assert len(plan["leads"]) == 2
    assert {row["company_name"] for row in plan["leads"]} == {"Co A", "Co B"}
    assert {row["drive_id"] for row in plan["leads"]} == {"d1", "d2"}


def test_plan_empty_when_no_candidates():
    assert rpd.plan_reextract([]) == {"leads": []}


# --- _fetch_candidates: DB filter --------------------------------------------


class _FakeLeadsResult:
    def __init__(self, leads):
        self._leads = leads

    def scalars(self):
        return self

    def all(self):
        return self._leads


class _FakeSession:
    def __init__(self, leads):
        self._leads = leads
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeLeadsResult(self._leads)

    async def commit(self):
        self.committed += 1


def test_fetch_candidates_returns_whatever_the_query_yields():
    """The isnot(None)/is_(None) filtering happens in SQL -- this only
    confirms the function wires the query through, owner-agnostic (no
    owner_email clause), matching the issue's 'firm-wide, all owners' ask."""
    leads = [_lead("alice@raed.vc"), _lead("bob@raed.vc")]
    session = _FakeSession(leads)

    result = asyncio.run(rpd._fetch_candidates(session))
    assert result == leads


# --- apply_reextract ----------------------------------------------------------


class _FakeDriveService:
    """Stands in for the Drive client -- _download_pdf is stubbed out below,
    so this only needs to exist as a distinguishable object."""


def _stub_pipeline(monkeypatch, *, download_side_effect=None, extracted_text_by_drive_id=None):
    """Stubs _download_pdf (network) and extract_text_from_pdf (OCR/PyMuPDF)
    so apply_reextract can be tested without touching Drive or Tesseract."""
    calls = {"downloaded": [], "extracted": []}

    def _fake_download_pdf(service, file_id, dest):
        calls["downloaded"].append(file_id)
        if download_side_effect and file_id in download_side_effect:
            raise download_side_effect[file_id]
        dest.write_bytes(b"%PDF-fake")

    def _fake_extract(path):
        calls["extracted"].append(path.name)
        # Keyed by drive_id via the temp filename set in apply_reextract.
        for drive_id, text in (extracted_text_by_drive_id or {}).items():
            if path.name.startswith(drive_id) or drive_id in str(path):
                return text
        return ""

    import app.tasks.sync_pitch_decks as sync_module
    monkeypatch.setattr(sync_module, "_download_pdf", _fake_download_pdf)
    monkeypatch.setattr(rpd, "extract_text_from_pdf", _fake_extract)

    queued = []
    fake_task_module = SimpleNamespace(assess_lead_task=SimpleNamespace(delay=lambda lead_id: queued.append(lead_id)))
    monkeypatch.setitem(sys.modules, "app.tasks.assess_lead", fake_task_module)

    return calls, queued


def test_apply_reextract_unsticks_lead_with_new_text(monkeypatch):
    lead = _lead("alice@raed.vc", "Co A", drive_id="drive-a", filename="drive-a-deck.pdf")
    session = _FakeSession([lead])
    calls, queued = _stub_pipeline(monkeypatch, extracted_text_by_drive_id={"drive-a": "Recovered OCR text"})

    result = asyncio.run(rpd.apply_reextract(session, _FakeDriveService(), [lead]))

    assert result == {"unstuck": 1, "still_empty": 0, "failed": 0}
    assert lead.pitch_deck_text == "Recovered OCR text"
    assert lead.pitch_deck_ingested_at is not None
    assert session.committed == 1
    assert queued == [str(lead.id)]
    assert calls["downloaded"] == ["drive-a"]


def test_apply_reextract_leaves_lead_alone_when_still_no_text(monkeypatch):
    lead = _lead("alice@raed.vc", "Co A", drive_id="drive-a", filename="drive-a-deck.pdf")
    session = _FakeSession([lead])
    _stub_pipeline(monkeypatch, extracted_text_by_drive_id={})  # extraction still yields ""

    result = asyncio.run(rpd.apply_reextract(session, _FakeDriveService(), [lead]))

    assert result == {"unstuck": 0, "still_empty": 1, "failed": 0}
    assert lead.pitch_deck_text is None
    assert session.committed == 0


def test_apply_reextract_one_failure_does_not_abort_the_batch(monkeypatch):
    ok_lead = _lead("alice@raed.vc", "Co A", drive_id="drive-a", filename="drive-a-deck.pdf")
    bad_lead = _lead("bob@raed.vc", "Co B", drive_id="drive-b", filename="drive-b-deck.pdf")
    session = _FakeSession([ok_lead, bad_lead])
    calls, queued = _stub_pipeline(
        monkeypatch,
        download_side_effect={"drive-b": RuntimeError("Drive 403")},
        extracted_text_by_drive_id={"drive-a": "Recovered text"},
    )

    result = asyncio.run(rpd.apply_reextract(session, _FakeDriveService(), [ok_lead, bad_lead]))

    assert result == {"unstuck": 1, "still_empty": 0, "failed": 1}
    assert ok_lead.pitch_deck_text == "Recovered text"
    assert bad_lead.pitch_deck_text is None
    assert queued == [str(ok_lead.id)]


# --- main(): dry-run vs --commit, and the missing-SA no-op -------------------


def _stub_session(monkeypatch, leads):
    session = _FakeSession(leads)
    monkeypatch.setattr(rpd, "AsyncSessionLocal", lambda: session)
    return session


def test_main_noops_when_service_account_unset(monkeypatch, capsys):
    monkeypatch.setattr(rpd.settings, "google_service_account_json", "")

    exit_code = rpd.main([])

    assert exit_code == 0
    assert "GOOGLE_SERVICE_ACCOUNT_JSON not set" in capsys.readouterr().out


def test_main_dry_run_makes_no_downloads_or_writes(monkeypatch, capsys):
    monkeypatch.setattr(rpd.settings, "google_service_account_json", "{}")
    lead = _lead("alice@raed.vc", "Co A", drive_id="drive-a")
    session = _stub_session(monkeypatch, [lead])
    calls, queued = _stub_pipeline(monkeypatch, extracted_text_by_drive_id={"drive-a": "text"})

    exit_code = rpd.main([])

    assert exit_code == 0
    assert session.committed == 0
    assert calls["downloaded"] == []
    assert queued == []
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Co A" in out


def test_main_commit_with_nothing_to_do_makes_no_writes(monkeypatch, capsys):
    monkeypatch.setattr(rpd.settings, "google_service_account_json", "{}")
    session = _stub_session(monkeypatch, [])

    exit_code = rpd.main(["--commit"])

    assert exit_code == 0
    assert session.committed == 0
    assert "Nothing to do." in capsys.readouterr().out


def test_main_commit_downloads_extracts_and_queues(monkeypatch, capsys):
    monkeypatch.setattr(rpd.settings, "google_service_account_json", "{}")
    lead = _lead("alice@raed.vc", "Co A", drive_id="drive-a", filename="drive-a-deck.pdf")
    session = _stub_session(monkeypatch, [lead])
    calls, queued = _stub_pipeline(monkeypatch, extracted_text_by_drive_id={"drive-a": "Recovered text"})

    import app.tasks.sync_pitch_decks as sync_module
    monkeypatch.setattr(sync_module, "_drive_service", lambda: _FakeDriveService())

    exit_code = rpd.main(["--commit"])

    assert exit_code == 0
    assert lead.pitch_deck_text == "Recovered text"
    assert session.committed == 1
    assert queued == [str(lead.id)]
    assert "Done." in capsys.readouterr().out
