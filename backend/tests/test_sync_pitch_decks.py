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
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services.pitch_deck import find_lead_match, match_filename_to_lead
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


class TestMatchFilenameToLead:
    """Deep coverage for the matcher hardening (issue #44).

    Concrete motivating bug: a deck named e.g. 'Ailoo Pitch Deck.pdf' failed
    to match a lead named 'Ailoo' because the old matcher didn't strip filler
    tokens or entity suffixes before comparing, so the normalized filename
    ('ailoo pitch deck') never got close enough to the lead's normalized name
    ('ailoo') to clear the fuzzy cutoff.
    """

    def _leads(self, *names):
        return [SimpleNamespace(company_name=n) for n in names]

    def test_ailoo_pitch_deck_matches_bare_company_name(self):
        leads = self._leads("Ailoo", "Some Other Startup")
        assert match_filename_to_lead("Ailoo Pitch Deck.pdf", leads) is leads[0]

    def test_ailoo_versioned_filename_matches(self):
        leads = self._leads("Ailoo", "Some Other Startup")
        assert match_filename_to_lead("ailoo_v2.pdf", leads) is leads[0]

    def test_ailoo_technologies_filename_matches_bare_lead_name(self):
        leads = self._leads("Ailoo", "Some Other Startup")
        assert match_filename_to_lead("Ailoo Technologies.pdf", leads) is leads[0]

    def test_bare_ailoo_filename_matches_lead_with_entity_suffix(self):
        # Reverse direction: the lead's own company_name carries the suffix.
        leads = self._leads("Ailoo Technologies", "Some Other Startup")
        assert match_filename_to_lead("Ailoo.pdf", leads) is leads[0]

    def test_filler_and_date_and_suffix_tokens_all_stripped(self):
        leads = self._leads("Ailoo", "Some Other Startup")
        assert match_filename_to_lead("Ailoo_Deck_Final_2024-05-01_v3.pdf", leads) is leads[0]
        assert match_filename_to_lead("Ailoo FZ-LLC.pdf", leads) is leads[0]
        assert match_filename_to_lead("RAED - Ailoo - Presentation Draft.pdf", leads) is leads[0]

    def test_near_miss_does_not_match(self):
        """'Aileen' is a genuinely different company -- must not attach to it,
        and vice-versa (each file should land on its own lead only)."""
        leads = self._leads("Ailoo", "Aileen")
        assert match_filename_to_lead("Aileen Pitch Deck.pdf", leads) is leads[1]
        assert match_filename_to_lead("Ailoo Pitch Deck.pdf", leads) is leads[0]
        assert match_filename_to_lead("Aileen.pdf", leads) is leads[1]

    def test_ambiguous_match_across_two_leads_is_left_unmatched(self):
        """Two distinct leads that are each equally (fuzzy-)close to the same
        filename: neither is confident enough to pick over the other, so this
        must be left unmatched -- false matches are worse than misses."""
        leads = self._leads("Ailooza", "Ailoozb")
        result = find_lead_match("Ailooze.pdf", leads)
        assert result.lead is None
        # Both leads should still show up as diagnostic candidates.
        assert {c.company_name for c in result.candidates} == {"Ailooza", "Ailoozb"}

    def test_unmatched_result_reports_closest_candidates_with_scores(self):
        leads = self._leads("Ailoo", "Hadawi", "Acme Deep Tech")
        result = find_lead_match("Totally Unrelated Filename.pdf", leads)
        assert result.lead is None
        assert len(result.candidates) <= 3
        assert all(0.0 <= c.score <= 1.0 for c in result.candidates)
        scores = [c.score for c in result.candidates]
        assert scores == sorted(scores, reverse=True)


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


def test_ingest_commits_before_queuing_reassessment(monkeypatch):
    """assess_lead_task re-fetches the lead from the DB at task start, so the
    commit must land before delay() is called -- otherwise a worker can pick
    up the task and re-assess with pitch_deck_text still NULL."""
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "clean deck text")

    events = []

    class _OrderTrackingSession(_FakeSession):
        async def commit(self):
            events.append("commit")

    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: events.append("delay"))

    lead = _fake_lead()
    db = _OrderTrackingSession(has_card=True)
    drive_file = {"id": "file123", "name": "Acme.pdf"}

    asyncio.run(spd._ingest_from_drive(db, None, lead, drive_file))

    assert events == ["commit", "delay"]


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


class _FakeLeadsResult:
    def __init__(self, leads):
        self._leads = leads

    def scalars(self):
        return self

    def all(self):
        return self._leads


class _FakeRunSession:
    """Stands in for the CelerySessionLocal() context manager used by
    _run(): the first execute() (select(Lead)) answers with `leads`, every
    later execute() (the per-match assessment-card check) answers with
    `has_card`."""

    def __init__(self, leads, has_card: bool = False):
        self._leads = leads
        self._has_card = has_card

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _FakeLeadsResult(self._leads)
        assert entity is AssessmentCard
        return _FakeCardResult(uuid.uuid4() if self._has_card else None)

    async def commit(self):
        pass


def test_run_does_not_reingest_lead_with_deck_text_but_no_drive_id(monkeypatch):
    """A lead ingested via the local scripts/ingest_pitch_decks.py flow has
    pitch_deck_text set but no pitch_deck_drive_id (LeadCard's "on file,
    sync pending" state). _run() must not match it to a Drive file, so its
    existing text isn't overwritten and no re-assessment is queued."""
    already_decked = SimpleNamespace(
        id=uuid.uuid4(),
        pitch_deck_drive_id=None,
        pitch_deck_filename="Acme-local-upload.pdf",
        pitch_deck_text="existing local deck text",
        pitch_deck_ingested_at=None,
        company_name="Acme Deep Tech",
    )

    monkeypatch.setattr(spd, "_drive_service", lambda: None)
    monkeypatch.setattr(
        spd, "_list_pdfs_in_folder", lambda service, folder_id: [{"id": "file123", "name": "Acme.pdf"}]
    )
    monkeypatch.setattr(spd, "CelerySessionLocal", lambda: _FakeRunSession([already_decked]))
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "re-downloaded text")

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(spd._run())

    assert result == {
        "drive_files": 1,
        "matched": 0,
        "unmatched": 1,
        "failed": 0,
        "reassessments_queued": 0,
        "unmatched_files": [{"name": "Acme.pdf", "candidates": []}],
    }
    assert queued == []
    assert already_decked.pitch_deck_text == "existing local deck text"
    assert already_decked.pitch_deck_drive_id is None


def test_run_second_pass_with_no_new_files_is_idempotent(monkeypatch):
    """A lead that already has a Drive-matched deck (pitch_deck_drive_id set)
    must not be re-matched, re-downloaded, or re-queued on a subsequent run
    over the same Drive folder contents."""
    already_synced = SimpleNamespace(
        id=uuid.uuid4(),
        pitch_deck_drive_id="file123",
        pitch_deck_filename="Acme.pdf",
        pitch_deck_text="existing synced text",
        pitch_deck_ingested_at="already-set",
        company_name="Acme Deep Tech",
    )

    monkeypatch.setattr(spd, "_drive_service", lambda: None)
    monkeypatch.setattr(
        spd, "_list_pdfs_in_folder", lambda service, folder_id: [{"id": "file123", "name": "Acme.pdf"}]
    )
    monkeypatch.setattr(spd, "CelerySessionLocal", lambda: _FakeRunSession([already_synced]))

    def _boom_download(*_args, **_kwargs):
        raise AssertionError("must not re-download an already-synced deck")

    monkeypatch.setattr(spd, "_download_pdf", _boom_download)

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(spd._run())

    assert result["matched"] == 0
    assert result["unmatched"] == 1
    assert result["failed"] == 0
    assert result["reassessments_queued"] == 0
    assert queued == []
    assert already_synced.pitch_deck_text == "existing synced text"


def test_one_failed_download_does_not_abort_remaining_files(monkeypatch):
    """A Drive API hiccup on one file must not stop the rest of the batch
    from being matched, downloaded, and (re-)assessed."""
    lead_a = _fake_lead()
    lead_a.company_name = "Ailoo"
    lead_b = _fake_lead()
    lead_b.company_name = "Hadawi"

    monkeypatch.setattr(spd, "_drive_service", lambda: None)
    monkeypatch.setattr(
        spd,
        "_list_pdfs_in_folder",
        lambda service, folder_id: [
            {"id": "bad", "name": "Ailoo.pdf"},
            {"id": "good", "name": "Hadawi.pdf"},
        ],
    )
    monkeypatch.setattr(spd, "CelerySessionLocal", lambda: _FakeRunSession([lead_a, lead_b], has_card=True))

    def _download(service, file_id, dest):
        if file_id == "bad":
            raise RuntimeError("simulated Drive download failure")
        dest.write_bytes(b"%PDF-fake")

    monkeypatch.setattr(spd, "_download_pdf", _download)
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "clean deck text")

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(spd._run())

    assert result["matched"] == 1
    assert result["failed"] == 1
    assert result["unmatched"] == 0
    assert result["reassessments_queued"] == 1
    assert queued == [str(lead_b.id)]
    assert lead_b.pitch_deck_text == "clean deck text"
    assert lead_a.pitch_deck_text is None
    assert lead_a.pitch_deck_drive_id is None
