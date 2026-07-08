"""
Tests for the scheduled Drive->lead pitch-deck sync task.

Covers the two behaviors called out in the issue: (1) the task must no-op
rather than crash the worker when GOOGLE_SERVICE_ACCOUNT_JSON isn't set, and
(2) a matched lead only gets queued for re-assessment if it was already
assessed -- a brand-new lead's first assessment (which will see the deck)
comes from the normal Copper-import flow, so re-queuing here would just be
duplicate work.
"""
import asyncio
import uuid
from types import SimpleNamespace

from app.config import settings
from app.services.pitch_deck import match_filename_to_lead
from app.tasks import sync_pitch_decks as spd
from app.tasks.assess_lead import assess_lead_task


def test_skip_when_google_credentials_unset(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", "")
    result = spd.sync_pitch_decks_task()
    assert result == {"skipped": "GOOGLE_SERVICE_ACCOUNT_JSON not set"}


def test_match_filename_to_lead_exact_and_fuzzy_and_miss():
    leads = [
        SimpleNamespace(company_name="Acme Deep Tech"),
        SimpleNamespace(company_name="Hadawi"),
    ]
    assert match_filename_to_lead("Hadawi.pdf", leads) is leads[1]
    assert match_filename_to_lead("Acme Deep Tech.pdf", leads) is leads[0]
    assert match_filename_to_lead("Totally Unrelated Co.pdf", leads) is None


def _fake_lead():
    return SimpleNamespace(
        id=uuid.uuid4(),
        pitch_deck_drive_id=None,
        pitch_deck_filename=None,
        pitch_deck_text=None,
        pitch_deck_ingested_at=None,
    )


class _FakeCardResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Stands in for AsyncSession: execute() answers the assessment-card
    existence check with whatever `has_card` says; commit() is a no-op."""

    def __init__(self, has_card: bool):
        self._has_card = has_card

    async def execute(self, _query):
        return _FakeCardResult(uuid.uuid4() if self._has_card else None)

    async def commit(self):
        pass


def _run_ingest(monkeypatch, *, has_card: bool, extracted_text: str):
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: extracted_text)

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    lead = _fake_lead()
    db = _FakeSession(has_card=has_card)
    drive_file = {"id": "file123", "name": "Acme.pdf"}

    requeued = asyncio.run(spd._ingest_from_drive(db, None, lead, drive_file))
    return lead, requeued, queued


def test_ingest_queues_reassessment_when_lead_already_assessed(monkeypatch):
    lead, requeued, queued = _run_ingest(monkeypatch, has_card=True, extracted_text="clean deck text")

    assert requeued is True
    assert queued == [str(lead.id)]
    assert lead.pitch_deck_text == "clean deck text"
    assert lead.pitch_deck_drive_id == "file123"
    assert lead.pitch_deck_filename == "Acme.pdf"
    assert lead.pitch_deck_ingested_at is not None


def test_ingest_skips_reassessment_when_never_assessed(monkeypatch):
    lead, requeued, queued = _run_ingest(monkeypatch, has_card=False, extracted_text="clean deck text")

    assert requeued is False
    assert queued == []
    assert lead.pitch_deck_text == "clean deck text"
    assert lead.pitch_deck_drive_id == "file123"


def test_ingest_does_not_store_garbled_text_but_still_records_drive_id(monkeypatch):
    lead, requeued, queued = _run_ingest(monkeypatch, has_card=True, extracted_text="")

    assert requeued is False
    assert queued == []
    assert lead.pitch_deck_text is None
    assert lead.pitch_deck_ingested_at is None
    # Drive id/filename are still recorded so the next run's idempotency
    # check (skip leads with pitch_deck_drive_id set) doesn't re-download
    # and re-OCR a deck that's known to extract as garbage every cycle.
    assert lead.pitch_deck_drive_id == "file123"
