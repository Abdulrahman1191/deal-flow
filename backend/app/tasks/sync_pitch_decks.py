from __future__ import annotations
"""
Scheduled Drive→lead pitch-deck sync.

Runs on Celery beat (see celery_app.py) so decks land on leads automatically
instead of requiring someone to run scripts/sync_drive_to_db.py or
ingest_pitch_decks.py locally. Every cycle:

  1. Lists PDFs in settings.drive_pitch_deck_folder_id via a Google service
     account (read-only scope — no OAuth browser flow needed, unlike the
     local scripts).
  2. Matches each file to a lead with no deck yet (no drive id AND no deck
     text — a lead ingested locally via scripts/ingest_pitch_decks.py has
     text but no drive id and must not be re-matched), reusing
     app.services.pitch_deck.match_filename_to_lead.
  3. Downloads + extracts text (same PyMuPDF/OCR/garble-guard pipeline as the
     local scripts) and stores it on the lead.
  4. Queues a re-assessment ONLY if the lead already had an assessment card
     — a brand-new lead gets assessed with its deck via the normal
     sync_copper import flow, so re-queuing here would just duplicate work.

Gracefully no-ops (logs one line, returns) when GOOGLE_SERVICE_ACCOUNT_JSON
isn't set — expected until a maintainer adds the secret post-merge.
"""
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import CelerySessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services.pitch_deck import extract_text_from_pdf, match_filename_to_lead
from app.tasks.celery_app import celery

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _drive_service():
    """Build a Drive v3 client from the service-account JSON in settings.

    Imports the Google client libraries lazily so the app/worker can boot
    without them installed when Drive sync isn't configured.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(settings.google_service_account_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_pdfs_in_folder(service, folder_id: str) -> list[dict]:
    """Paginated list of every PDF in the folder (non-recursive — flat layout)."""
    files: list[dict] = []
    page_token = None
    q = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    while True:
        resp = service.files().list(
            q=q,
            fields="nextPageToken, files(id, name)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def _download_pdf(service, file_id: str, dest: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


async def _ingest_from_drive(db: AsyncSession, service, lead: Lead, drive_file: dict) -> bool:
    """Download, extract, and store a matched Drive file on its lead.

    Returns True if a re-assessment was queued (lead already had a card).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_path = Path(tmp_dir) / drive_file["name"]
        _download_pdf(service, drive_file["id"], pdf_path)
        text = extract_text_from_pdf(pdf_path)

    lead.pitch_deck_drive_id = drive_file["id"]
    lead.pitch_deck_filename = drive_file["name"]

    should_requeue = False
    if text:
        lead.pitch_deck_text = text
        lead.pitch_deck_ingested_at = datetime.now(timezone.utc)

        existing_card = await db.execute(
            select(AssessmentCard.id).where(AssessmentCard.lead_id == lead.id).limit(1)
        )
        should_requeue = existing_card.scalar_one_or_none() is not None

    await db.commit()

    if should_requeue:
        # Queued only after commit lands: assess_lead_task re-fetches the
        # lead from the DB at task start, so queuing before commit risks a
        # worker picking it up and re-assessing with pitch_deck_text still
        # NULL -- and a permanent stale assessment, since the next sync run
        # skips this lead once pitch_deck_drive_id is set.
        from app.tasks.assess_lead import assess_lead_task
        assess_lead_task.delay(str(lead.id))

    return should_requeue


async def _run() -> dict:
    service = _drive_service()
    drive_files = _list_pdfs_in_folder(service, settings.drive_pitch_deck_folder_id)

    async with CelerySessionLocal() as db:
        all_leads = (await db.execute(select(Lead))).scalars().all()
        # Idempotency: a lead that already has a Drive-matched deck is never
        # re-matched, so a re-run with no new files changes nothing. Leads
        # ingested via the local scripts/ingest_pitch_decks.py flow have
        # pitch_deck_text set but no pitch_deck_drive_id ("on file, sync
        # pending" — see LeadCard.tsx) — exclude those too, since they
        # already have a deck and re-ingesting would overwrite it and queue
        # a spurious re-assessment.
        remaining_leads = [
            l for l in all_leads if not l.pitch_deck_drive_id and not l.pitch_deck_text
        ]

        matched, unmatched, requeued = 0, 0, 0
        for drive_file in drive_files:
            lead = match_filename_to_lead(drive_file["name"], remaining_leads)
            if not lead:
                unmatched += 1
                print(f"[sync_pitch_decks] no lead match for Drive file {drive_file['name']!r}")
                continue
            remaining_leads.remove(lead)
            matched += 1
            if await _ingest_from_drive(db, service, lead, drive_file):
                requeued += 1

    result = {
        "drive_files": len(drive_files),
        "matched": matched,
        "unmatched": unmatched,
        "reassessments_queued": requeued,
    }
    print(f"[sync_pitch_decks] {result}")
    return result


@celery.task(bind=True, max_retries=3, default_retry_delay=120)
def sync_pitch_decks_task(self) -> dict:
    if not settings.google_service_account_json:
        print("[sync_pitch_decks] GOOGLE_SERVICE_ACCOUNT_JSON not set; skipping")
        return {"skipped": "GOOGLE_SERVICE_ACCOUNT_JSON not set"}
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(exc=exc)
