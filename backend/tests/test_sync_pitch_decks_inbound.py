"""Tests for the inbound-folder sweep (issue #189).

The info@ intake agent auto-saves every email attachment into a second Drive
folder ("Inbound Pitch Decks"), named "<sender domain> — <original
filename>.pdf". These tests cover: the additive/no-op-when-unset contract,
the sender-domain match signal (and generic-domain exclusion), skipping
non-PDF/non-presentation files without downloading them, not re-downloading
an already-processed file until its re-check window lapses, and that nothing
in the source folder is ever written to.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.config import settings
from app.models.assessment import AssessmentCard
from app.models.processed_drive_file import ProcessedDriveFile
from app.services.pitch_deck import find_lead_match, parse_inbound_filename
from app.tasks import sync_pitch_decks as spd
from app.tasks.assess_lead import assess_lead_task


def _lead(**overrides):
    base = dict(
        id=uuid.uuid4(),
        company_name="Zylo Corp",
        website=None,
        raw_copper_data=None,
        pitch_deck_drive_id=None,
        pitch_deck_filename=None,
        pitch_deck_text=None,
        pitch_deck_ingested_at=None,
        description=None,
        status="pending",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------- parse_inbound_filename / find_lead_match sender_domain ----------


def test_parse_inbound_filename_splits_on_em_dash_separator():
    domain, rest = parse_inbound_filename("verdantimpact.com — VERDANT DECK Series A.pdf")
    assert domain == "verdantimpact.com"
    assert rest == "VERDANT DECK Series A.pdf"


def test_parse_inbound_filename_without_separator_falls_back_unchanged():
    domain, rest = parse_inbound_filename("Acme.pdf")
    assert domain is None
    assert rest == "Acme.pdf"


def test_parse_inbound_filename_rejects_non_domain_prefix():
    # A real filename that happens to contain " — " but isn't the agent's
    # convention (left side isn't a domain) must fall back untouched.
    domain, rest = parse_inbound_filename("Notes — Q3 Deck.pdf")
    assert domain is None
    assert rest == "Notes — Q3 Deck.pdf"


def test_domain_match_attaches_even_when_filename_alone_would_not():
    lead = _lead(company_name="Zylo Corp", website="https://acme.com/about")
    result = find_lead_match("Acme Deck.pdf", [lead], sender_domain="acme.com")
    assert result.lead is lead


def test_domain_match_against_contact_email_domain():
    lead = _lead(
        company_name="Zylo Corp", raw_copper_data={"recipient_email": "founder@acme.com"},
    )
    result = find_lead_match("Random Filename.pdf", [lead], sender_domain="acme.com")
    assert result.lead is lead


def test_generic_sender_domain_is_ignored_and_falls_back_to_filename():
    lead = _lead(company_name="Zylo Corp", website="https://gmail.com")
    result = find_lead_match("Something.pdf", [lead], sender_domain="gmail.com")
    assert result.lead is None


def test_domain_shared_by_two_leads_is_ambiguous_on_its_own():
    a = _lead(company_name="Zylo Corp", website="https://acme.com")
    b = _lead(company_name="Other Corp", website="https://acme.com")
    result = find_lead_match("Totally Unrelated.pdf", [a, b], sender_domain="acme.com")
    assert result.lead is None


# ---------- _sweep_inbound_folder ----------


class _ScalarOneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeInboundSession:
    """Routes select(ProcessedDriveFile) to the canned existing row, and
    select(AssessmentCard) to has_card -- mirrors the entity-routed fake
    session pattern used elsewhere in this test suite (test_duplicates.py)."""

    def __init__(self, existing_processed=None, has_card=True):
        self._existing_processed = existing_processed
        self._has_card = has_card
        self.added: list = []
        self.committed = 0

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is ProcessedDriveFile:
            return _ScalarOneResult(self._existing_processed)
        assert entity is AssessmentCard
        return _ScalarOneResult(uuid.uuid4() if self._has_card else None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def _boom_download(*_args, **_kwargs):
    raise AssertionError("must not download a file that should have been skipped")


def test_mp4_and_pptx_are_skipped_counted_and_not_downloaded(monkeypatch):
    monkeypatch.setattr(
        spd,
        "_list_files_in_folder",
        lambda service, folder_id: [
            {"id": "v1", "name": "gmail.com — MohammedBinManiaCV.mp4", "mimeType": "video/mp4"},
            {
                "id": "p1",
                "name": "admyra.ai — Admyra-TeaserDeck.pptx",
                "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            },
        ],
    )
    monkeypatch.setattr(spd, "_download_pdf", _boom_download)

    db = _FakeInboundSession()
    result = asyncio.run(spd._sweep_inbound_folder(db, None, "folder-id", [], None))

    assert result["scanned"] == 2
    assert result["skipped_non_pdf"] == 1
    assert result["skipped_presentation"] == 1
    assert result["matched"] == 0
    assert result["unmatched"] == 0
    assert db.added == []
    assert db.committed == 0


def test_domain_matched_pdf_attaches_and_records_processed_outcome(monkeypatch):
    lead = _lead(company_name="Zylo Corp", website="https://verdantimpact.com")

    monkeypatch.setattr(
        spd,
        "_list_files_in_folder",
        lambda service, folder_id: [
            {"id": "file1", "name": "verdantimpact.com — Deck.pdf", "mimeType": "application/pdf"}
        ],
    )
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "deck text")

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    remaining = [lead]
    db = _FakeInboundSession(has_card=True)
    result = asyncio.run(spd._sweep_inbound_folder(db, None, "folder-id", remaining, None))

    assert result["matched"] == 1
    assert result["reassessments_queued"] == 1
    assert queued == [str(lead.id)]
    assert lead.pitch_deck_drive_id == "file1"
    assert lead not in remaining
    assert len(db.added) == 1
    assert db.added[0].drive_file_id == "file1"
    assert db.added[0].outcome == "matched"


def test_unmatched_pdf_is_recorded_and_not_redownloaded_next_run(monkeypatch):
    monkeypatch.setattr(
        spd,
        "_list_files_in_folder",
        lambda service, folder_id: [
            {"id": "file1", "name": "gmail.com — Nothing Related.pdf", "mimeType": "application/pdf"}
        ],
    )
    monkeypatch.setattr(settings, "deck_match_verify_enabled", False)

    lead = _lead(company_name="Zylo Corp")
    db = _FakeInboundSession()
    result = asyncio.run(spd._sweep_inbound_folder(db, None, "folder-id", [lead], None))

    assert result["unmatched"] == 1
    assert len(db.added) == 1
    recorded = db.added[0]
    assert recorded.drive_file_id == "file1"
    assert recorded.outcome == "unmatched"

    # Second run: the same file is already recorded as unmatched, fresh
    # within the re-check window -- must be skipped, not re-downloaded.
    monkeypatch.setattr(spd, "_download_pdf", _boom_download)
    recorded.processed_at = datetime.now(timezone.utc)
    db2 = _FakeInboundSession(existing_processed=recorded)
    result2 = asyncio.run(spd._sweep_inbound_folder(db2, None, "folder-id", [lead], None))

    assert result2["skipped_already_processed"] == 1
    assert result2["unmatched"] == 0
    assert db2.added == []


def test_unmatched_pdf_is_rechecked_after_recheck_window_lapses(monkeypatch):
    monkeypatch.setattr(
        spd,
        "_list_files_in_folder",
        lambda service, folder_id: [
            {"id": "file1", "name": "verdantimpact.com — Deck.pdf", "mimeType": "application/pdf"}
        ],
    )
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "deck text")
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: None)

    # A lead arrived in Copper AFTER its deck did (issue #189's whole point):
    # first pass left the file unmatched (no leads existed yet); 31 days
    # later the matching lead now exists and must be picked up.
    stale_row = ProcessedDriveFile(
        id=uuid.uuid4(),
        drive_file_id="file1",
        outcome="unmatched",
        processed_at=datetime.now(timezone.utc) - timedelta(days=31),
    )
    lead = _lead(company_name="Zylo Corp", website="https://verdantimpact.com")
    db = _FakeInboundSession(existing_processed=stale_row, has_card=True)

    result = asyncio.run(spd._sweep_inbound_folder(db, None, "folder-id", [lead], None))

    assert result["skipped_already_processed"] == 0
    assert result["matched"] == 1
    assert lead.pitch_deck_drive_id == "file1"


def test_force_rechecks_a_recently_unmatched_file(monkeypatch):
    monkeypatch.setattr(
        spd,
        "_list_files_in_folder",
        lambda service, folder_id: [
            {"id": "file1", "name": "verdantimpact.com — Deck.pdf", "mimeType": "application/pdf"}
        ],
    )
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "deck text")
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: None)

    fresh_row = ProcessedDriveFile(
        id=uuid.uuid4(), drive_file_id="file1", outcome="unmatched",
        processed_at=datetime.now(timezone.utc),
    )
    lead = _lead(company_name="Zylo Corp", website="https://verdantimpact.com")
    db = _FakeInboundSession(existing_processed=fresh_row, has_card=True)

    result = asyncio.run(
        spd._sweep_inbound_folder(db, None, "folder-id", [lead], None, force=True)
    )

    assert result["skipped_already_processed"] == 0
    assert result["matched"] == 1


# ---------- additive / no-op-when-unset, and read-only Drive access ----------


def test_run_does_not_sweep_inbound_folder_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "drive_inbound_deck_folder_id", "")
    monkeypatch.setattr(spd, "_drive_service", lambda: None)
    monkeypatch.setattr(spd, "_list_pdfs_in_folder", lambda service, folder_id: [])

    def _boom(*_a, **_k):
        raise AssertionError("must not list the inbound folder when it isn't configured")

    monkeypatch.setattr(spd, "_list_files_in_folder", _boom)

    class _EmptyLeadsSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, query):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    monkeypatch.setattr(spd, "CelerySessionLocal", lambda: _EmptyLeadsSession())

    result = asyncio.run(spd._run())

    assert "inbound" not in result
    assert result == {
        "drive_files": 0,
        "matched": 0,
        "unmatched": 0,
        "failed": 0,
        "reassessments_queued": 0,
        "unmatched_files": [],
    }


class _ReadOnlyDriveFilesResource:
    """Stands in for Drive's `service.files()` resource: only `.list()` and
    `.get_media()` are implemented -- anything else (update/delete/copy/
    rename, i.e. a write) raises, proving a caller never attempts one."""

    def __init__(self, listing):
        self._listing = listing

    def list(self, **_kwargs):
        return SimpleNamespace(execute=lambda: {"files": self._listing})

    def get_media(self, fileId):  # noqa: N803 - matches googleapiclient's call signature
        return SimpleNamespace(fileId=fileId)

    def __getattr__(self, name):
        raise AssertionError(f"read-only inbound folder: files().{name}() must never be called")


def test_list_files_in_folder_only_ever_calls_list():
    listing = [{"id": "file1", "name": "verdantimpact.com — Deck.pdf", "mimeType": "application/pdf"}]
    service = SimpleNamespace(files=lambda: _ReadOnlyDriveFilesResource(listing))

    files = spd._list_files_in_folder(service, "folder-id")

    assert files == listing


def test_inbound_sweep_never_calls_a_write_operation_on_the_drive_client(monkeypatch):
    listing = [{"id": "file1", "name": "verdantimpact.com — Deck.pdf", "mimeType": "application/pdf"}]
    service = SimpleNamespace(files=lambda: _ReadOnlyDriveFilesResource(listing))

    # _download_pdf is the only other place the sweep talks to `service`
    # (via get_media, also read-only) -- mocked here the same way every
    # other test in this suite mocks the actual byte transfer, so this test
    # isolates the claim under test: folder listing and match resolution
    # never reach for anything but the two read-only resource methods above.
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "deck text")
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: None)

    lead = _lead(company_name="Zylo Corp", website="https://verdantimpact.com")
    db = _FakeInboundSession(has_card=True)

    result = asyncio.run(spd._sweep_inbound_folder(db, service, "folder-id", [lead], None))

    assert result["matched"] == 1
