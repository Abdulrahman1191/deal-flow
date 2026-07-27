"""
Tests for the Copper "Pitch Deck" URL field ingestion sweep (issue #76).

Covers: parsing a Drive link out of the configured Copper custom field,
downloading+attaching it exactly like the Drive-folder sweep, skipping
non-Drive URLs and blanks, logging (and continuing past) Drive permission
errors, and idempotency/batch isolation in the full sweep.
"""
import asyncio
import uuid
from types import SimpleNamespace

from app.config import settings
from app.tasks import sync_pitch_decks as spd
from app.tasks.assess_lead import assess_lead_task

FIELD_ID = 757961


class _FakeSession:
    """Stands in for AsyncSession for _ingest_from_copper_link tests.
    require_existing_card=False means _ingest_from_drive never calls
    execute(), so a call here would indicate a regression."""

    async def execute(self, _query):
        raise AssertionError("must not query AssessmentCard when require_existing_card=False")

    async def commit(self):
        pass


class _FakeFilesGet:
    def __init__(self, name):
        self._name = name

    def execute(self):
        return {"id": "file123", "name": self._name}


class _FakeFiles:
    def __init__(self, name=None, error=None):
        self._name = name
        self._error = error

    def get(self, fileId, fields):
        if self._error:
            raise self._error
        return _FakeFilesGet(self._name)


class _FakeDriveService:
    def __init__(self, name=None, error=None):
        self._files = _FakeFiles(name, error)

    def files(self):
        return self._files


def _fake_lead_for_link(copper_id="c1"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        copper_id=copper_id,
        company_name="Acme",
        raw_copper_data=None,
        pitch_deck_drive_id=None,
        pitch_deck_filename=None,
        pitch_deck_text=None,
        pitch_deck_ingested_at=None,
    )


def _custom_fields_raw(value):
    return {"custom_fields": [{"custom_field_definition_id": FIELD_ID, "value": value}]}


def test_parse_drive_file_id_common_shapes():
    assert spd._parse_drive_file_id(
        "https://drive.google.com/file/d/1AbC-XyZ_9/view?usp=sharing"
    ) == "1AbC-XyZ_9"
    assert spd._parse_drive_file_id("https://drive.google.com/open?id=1AbC-XyZ_9") == "1AbC-XyZ_9"
    assert spd._parse_drive_file_id(
        "https://docs.google.com/presentation/d/1AbC-XyZ_9/edit#slide=id.p"
    ) == "1AbC-XyZ_9"


def test_parse_drive_file_id_rejects_non_drive_and_blank():
    assert spd._parse_drive_file_id("http://test.com") is None
    assert spd._parse_drive_file_id("") is None
    assert spd._parse_drive_file_id(None) is None
    assert spd._parse_drive_file_id("   ") is None


def test_pitch_deck_url_field_reads_configured_custom_field(monkeypatch):
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    raw = _custom_fields_raw("https://drive.google.com/file/d/abc123/view")
    assert spd._pitch_deck_url_field(raw) == "https://drive.google.com/file/d/abc123/view"


def test_pitch_deck_url_field_missing_or_blank(monkeypatch):
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    assert spd._pitch_deck_url_field(None) is None
    assert spd._pitch_deck_url_field({"custom_fields": []}) is None
    assert spd._pitch_deck_url_field(_custom_fields_raw("   ")) is None
    assert spd._pitch_deck_url_field(
        {"custom_fields": [{"custom_field_definition_id": 999, "value": "x"}]}
    ) is None


def test_ingest_from_copper_link_downloads_and_queues_assessment(monkeypatch):
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    raw = _custom_fields_raw("https://drive.google.com/file/d/file123/view")
    monkeypatch.setattr(spd, "fetch_lead_by_id", lambda copper_id: raw)
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "clean deck text")

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    lead = _fake_lead_for_link()
    service = _FakeDriveService(name="Acme.pdf")

    outcome = asyncio.run(spd._ingest_from_copper_link(_FakeSession(), service, lead))

    assert outcome == "ingested"
    assert lead.pitch_deck_drive_id == "file123"
    assert lead.pitch_deck_filename == "Acme.pdf"
    assert lead.pitch_deck_text == "clean deck text"
    assert lead.pitch_deck_ingested_at is not None
    # require_existing_card=False -- always queues, unlike the folder sweep,
    # since this channel exists specifically for leads stuck awaiting a deck.
    assert queued == [str(lead.id)]


def test_ingest_from_copper_link_skips_non_drive_url(monkeypatch):
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    monkeypatch.setattr(spd, "fetch_lead_by_id", lambda copper_id: _custom_fields_raw("http://test.com"))

    def _boom(*_args, **_kwargs):
        raise AssertionError("must not attempt to download a non-Drive URL")

    monkeypatch.setattr(spd, "_download_pdf", _boom)

    lead = _fake_lead_for_link()
    outcome = asyncio.run(spd._ingest_from_copper_link(_FakeSession(), _FakeDriveService(), lead))

    assert outcome == "skipped_non_drive"
    assert lead.pitch_deck_drive_id is None


def test_ingest_from_copper_link_no_link():
    lead = _fake_lead_for_link(copper_id=None)
    lead.raw_copper_data = {"custom_fields": []}
    outcome = asyncio.run(spd._ingest_from_copper_link(_FakeSession(), _FakeDriveService(), lead))
    assert outcome == "no_link"


def test_ingest_from_copper_link_falls_back_to_cached_raw_data_when_copper_read_fails(monkeypatch):
    """Fresh Copper read is preferred, but raw_copper_data.custom_fields is an
    acceptable source per the issue -- a Copper API hiccup must not turn into
    a false 'no_link'."""
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)

    def _boom(copper_id):
        raise RuntimeError("simulated Copper API hiccup")

    monkeypatch.setattr(spd, "fetch_lead_by_id", _boom)
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "clean deck text")
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: None)

    lead = _fake_lead_for_link()
    lead.raw_copper_data = _custom_fields_raw("https://drive.google.com/file/d/file123/view")
    service = _FakeDriveService(name="Acme.pdf")

    outcome = asyncio.run(spd._ingest_from_copper_link(_FakeSession(), service, lead))
    assert outcome == "ingested"


def test_ingest_from_copper_link_permission_error_logs_and_returns_no_access(monkeypatch, caplog):
    from googleapiclient.errors import HttpError

    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    raw = _custom_fields_raw("https://drive.google.com/file/d/file123/view")
    monkeypatch.setattr(spd, "fetch_lead_by_id", lambda copper_id: raw)

    class _FakeResp:
        status = 403
        reason = "Forbidden"

    error = HttpError(_FakeResp(), b'{"error": {"message": "no access"}}')
    service = _FakeDriveService(error=error)

    lead = _fake_lead_for_link()
    with caplog.at_level("WARNING"):
        outcome = asyncio.run(spd._ingest_from_copper_link(_FakeSession(), service, lead))

    assert outcome == "no_access"
    assert lead.pitch_deck_drive_id is None
    assert "SA lacks access to file123" in caplog.text
    assert spd.DECK_READER_SA_EMAIL in caplog.text


def test_ingest_from_copper_link_other_download_failure_is_caught(monkeypatch):
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    raw = _custom_fields_raw("https://drive.google.com/file/d/file123/view")
    monkeypatch.setattr(spd, "fetch_lead_by_id", lambda copper_id: raw)
    service = _FakeDriveService(error=RuntimeError("boom"))

    lead = _fake_lead_for_link()
    outcome = asyncio.run(spd._ingest_from_copper_link(_FakeSession(), service, lead))

    assert outcome == "failed"
    assert lead.pitch_deck_drive_id is None


class _FakeLeadsResult:
    def __init__(self, leads):
        self._leads = leads

    def scalars(self):
        return self

    def all(self):
        return self._leads


class _FakeBatchSession:
    def __init__(self, leads):
        self._leads = leads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeLeadsResult(self._leads)

    async def commit(self):
        pass


def test_run_copper_pitch_deck_links_batch_continues_past_failures(monkeypatch):
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    monkeypatch.setattr(spd, "_drive_service", lambda: _FakeDriveService(name="Deck.pdf"))

    good = _fake_lead_for_link(copper_id="good")
    bad = _fake_lead_for_link(copper_id="bad")
    non_drive = _fake_lead_for_link(copper_id="nd")
    no_link = _fake_lead_for_link(copper_id="nl")
    already = _fake_lead_for_link(copper_id="already")
    already.pitch_deck_drive_id = "existing-file"

    urls = {
        "good": "https://drive.google.com/file/d/good123/view",
        "bad": "https://drive.google.com/file/d/bad123/view",
        "nd": "http://test.com",
        "nl": "",
        "already": "https://drive.google.com/file/d/already123/view",
    }
    monkeypatch.setattr(spd, "fetch_lead_by_id", lambda copper_id: _custom_fields_raw(urls[copper_id]))
    monkeypatch.setattr(
        spd, "CelerySessionLocal", lambda: _FakeBatchSession([good, bad, non_drive, no_link, already])
    )

    def _download(service, file_id, dest):
        if file_id == "bad123":
            raise RuntimeError("simulated download failure")
        dest.write_bytes(b"%PDF-fake")

    monkeypatch.setattr(spd, "_download_pdf", _download)
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "clean deck text")

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    result = asyncio.run(spd._run_copper_pitch_deck_links())

    assert result["leads_checked"] == 5
    assert result["ingested"] == 1
    assert result["failed"] == 1
    assert result["skipped_non_drive"] == 1
    assert result["no_link"] == 1
    assert queued == [str(good.id)]
    assert good.pitch_deck_text == "clean deck text"
    assert bad.pitch_deck_drive_id is None
    # Already-attached lead is skipped entirely (idempotency) -- not touched,
    # not even attempted, so it contributes to none of the outcome buckets.
    assert already.pitch_deck_text is None


def test_sync_copper_pitch_deck_links_task_skips_when_google_credentials_unset(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", "")
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", FIELD_ID)
    result = spd.sync_copper_pitch_deck_links_task()
    assert result == {"skipped": "GOOGLE_SERVICE_ACCOUNT_JSON not set"}


def test_sync_copper_pitch_deck_links_task_skips_when_field_id_unset(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", "fake-json")
    monkeypatch.setattr(settings, "copper_cf_pitch_deck_url_id", 0)
    result = spd.sync_copper_pitch_deck_links_task()
    assert result == {"skipped": "COPPER_CF_PITCH_DECK_URL_ID not set"}
