"""
Tests for the on-demand per-lead pitch-deck fetch (issue #46).

sync_lead_pitch_deck() (app/tasks/sync_pitch_decks.py) powers the "Fetch
pitch deck" button / POST /leads/{id}/sync-pitch-deck. Unlike the scheduled
sweep, it must never raise -- every failure branch turns into a structured
diagnostic dict instead, so these tests exercise each branch directly
(mocking the same Drive pieces test_sync_pitch_decks.py mocks), then check
owner scoping through the real HTTP endpoint (same dependency-override
pattern as test_multiuser_access.py).
"""
import asyncio
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.services.auth import get_current_user
from app.tasks import sync_pitch_decks as spd
from app.tasks.assess_lead import assess_lead_task

client = TestClient(app)


def _fake_lead(**overrides):
    base = dict(
        id=uuid.uuid4(),
        company_name="Ailoo",
        pitch_deck_drive_id=None,
        pitch_deck_filename=None,
        pitch_deck_text=None,
        pitch_deck_ingested_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Answers the assessment-card existence check (only reached when
    require_existing_card=True); commit() is a no-op."""

    def __init__(self, has_card: bool = True):
        self._has_card = has_card

    async def execute(self, _query):
        return _FakeResult(uuid.uuid4() if self._has_card else None)

    async def commit(self):
        pass


# ---------- unconfigured ----------


def test_unconfigured_reports_reason_and_short_circuits(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", "")
    lead = _fake_lead()
    result = asyncio.run(spd.sync_lead_pitch_deck(_FakeSession(), lead))

    assert result["configured"] is False
    assert result["folder_readable"] is False
    assert result["attached"] is False
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in result["reason"]


# ---------- folder unreadable ----------


def test_folder_unreadable_surfaces_drive_error(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", '{"fake": "creds"}')
    monkeypatch.setattr(spd, "_drive_service", lambda: object())

    def _boom(service, folder_id):
        raise RuntimeError("403 caller does not have permission")

    monkeypatch.setattr(spd, "_list_pdfs_in_folder", _boom)

    lead = _fake_lead()
    result = asyncio.run(spd.sync_lead_pitch_deck(_FakeSession(), lead))

    assert result["configured"] is True
    assert result["folder_readable"] is False
    assert result["attached"] is False
    assert "403 caller does not have permission" in result["reason"]


# ---------- no match ----------


def test_no_matching_file_reports_closest_candidates(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", '{"fake": "creds"}')
    monkeypatch.setattr(spd, "_drive_service", lambda: object())
    monkeypatch.setattr(
        spd,
        "_list_pdfs_in_folder",
        lambda service, folder_id: [
            {"id": "1", "name": "Hadawi.pdf"},
            {"id": "2", "name": "Some Other Startup.pdf"},
        ],
    )

    lead = _fake_lead(company_name="Ailoo")
    result = asyncio.run(spd.sync_lead_pitch_deck(_FakeSession(), lead))

    assert result["folder_readable"] is True
    assert result["files_in_folder"] == 2
    assert result["matched_file"] is None
    assert result["attached"] is False
    assert set(result["closest_candidates"]) == {"Hadawi.pdf", "Some Other Startup.pdf"}
    assert "Ailoo" in result["reason"]


# ---------- successful attach ----------


def test_successful_attach_sets_fields_and_queues_one_reassessment(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", '{"fake": "creds"}')
    monkeypatch.setattr(spd, "_drive_service", lambda: object())
    monkeypatch.setattr(
        spd,
        "_list_pdfs_in_folder",
        lambda service, folder_id: [{"id": "file123", "name": "Ailoo.pdf"}],
    )
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "clean deck text")

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    lead = _fake_lead(company_name="Ailoo")
    # No existing assessment card -- the on-demand endpoint must still queue
    # a re-assessment (unlike the scheduled sweep, which only requeues an
    # already-assessed lead).
    result = asyncio.run(spd.sync_lead_pitch_deck(_FakeSession(has_card=False), lead))

    assert result["matched_file"] == "Ailoo.pdf"
    assert result["attached"] is True
    assert result["garbled"] is False
    assert result["extracted_chars"] == len("clean deck text")
    assert result["reassessment_queued"] is True
    assert queued == [str(lead.id)]
    assert lead.pitch_deck_text == "clean deck text"
    assert lead.pitch_deck_drive_id == "file123"


# ---------- garbled extraction ----------


def test_garbled_extraction_not_stored_but_drive_id_recorded(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", '{"fake": "creds"}')
    monkeypatch.setattr(spd, "_drive_service", lambda: object())
    monkeypatch.setattr(
        spd,
        "_list_pdfs_in_folder",
        lambda service, folder_id: [{"id": "file123", "name": "Ailoo.pdf"}],
    )
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "")  # garbled/empty

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    lead = _fake_lead(company_name="Ailoo")
    result = asyncio.run(spd.sync_lead_pitch_deck(_FakeSession(has_card=True), lead))

    assert result["garbled"] is True
    assert result["extracted_chars"] == 0
    assert result["reassessment_queued"] is False
    assert queued == []
    assert lead.pitch_deck_text is None
    # Drive id/filename still recorded so a re-fetch (with force) doesn't
    # need to guess which file it was.
    assert lead.pitch_deck_drive_id == "file123"


# ---------- idempotent ----------


def test_already_attached_short_circuits_without_touching_drive(monkeypatch):
    def _boom():
        raise AssertionError("must not contact Drive when already attached")

    monkeypatch.setattr(spd, "_drive_service", _boom)

    lead = _fake_lead(
        pitch_deck_drive_id="file123",
        pitch_deck_filename="Ailoo.pdf",
        pitch_deck_text="existing text",
    )
    result = asyncio.run(spd.sync_lead_pitch_deck(_FakeSession(), lead))

    assert result["attached"] is True
    assert result["matched_file"] == "Ailoo.pdf"
    assert result["reassessment_queued"] is False
    assert "already attached" in result["reason"]


def test_force_bypasses_idempotency_guard(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", '{"fake": "creds"}')
    monkeypatch.setattr(spd, "_drive_service", lambda: object())
    monkeypatch.setattr(
        spd,
        "_list_pdfs_in_folder",
        lambda service, folder_id: [{"id": "file456", "name": "Ailoo.pdf"}],
    )
    monkeypatch.setattr(spd, "_download_pdf", lambda service, file_id, dest: dest.write_bytes(b"%PDF-fake"))
    monkeypatch.setattr(spd, "extract_text_from_pdf", lambda path: "refreshed deck text")

    queued = []
    monkeypatch.setattr(assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    lead = _fake_lead(
        company_name="Ailoo",
        pitch_deck_drive_id="file123",
        pitch_deck_filename="Ailoo-old.pdf",
        pitch_deck_text="stale text",
    )
    result = asyncio.run(spd.sync_lead_pitch_deck(_FakeSession(has_card=False), lead, force=True))

    assert result["attached"] is True
    assert result["matched_file"] == "Ailoo.pdf"
    assert result["reassessment_queued"] is True
    assert queued == [str(lead.id)]
    assert lead.pitch_deck_text == "refreshed deck text"
    assert lead.pitch_deck_drive_id == "file456"


# ---------- owner scoping (through the real endpoint) ----------


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def test_sync_pitch_deck_endpoint_404s_for_a_lead_you_dont_own():
    class _EmptySession:
        async def execute(self, _query):
            return _FakeResult(None)

    async def _fake_get_db():
        yield _EmptySession()

    _auth_as("someone@raed.vc")
    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = client.post(f"/api/v1/leads/{uuid.uuid4()}/sync-pitch-deck")
    finally:
        _clear_auth()
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404
