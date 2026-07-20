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
     app.services.pitch_deck.find_lead_match. Only high-confidence matches
     attach; anything else is logged at WARNING with its closest candidates
     and surfaced in the run result for scripts/run_pitch_sync.py to report.
  3. Downloads + extracts text (same PyMuPDF/OCR/garble-guard pipeline as the
     local scripts) and stores it on the lead. A single file's download/
     extraction failure is caught and logged so it doesn't abort the rest of
     the run.
  4. Queues a re-assessment ONLY if the lead already had an assessment card
     — a brand-new lead gets assessed with its deck via the normal
     sync_copper import flow, so re-queuing here would just duplicate work.

Gracefully no-ops (logs one line, returns) when GOOGLE_SERVICE_ACCOUNT_JSON
isn't set — expected until a maintainer adds the secret post-merge.
"""
import asyncio
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import CelerySessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services.pitch_deck import MATCH_THRESHOLD, extract_text_from_pdf, find_lead_match
from app.tasks.celery_app import celery

logger = logging.getLogger(__name__)

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


async def _ingest_from_drive(
    db: AsyncSession, service, lead: Lead, drive_file: dict, *, require_existing_card: bool = True
) -> bool:
    """Download, extract, and store a matched Drive file on its lead.

    Returns True if a re-assessment was queued. When `require_existing_card`
    is True (the scheduled sweep's behavior), that only happens if the lead
    already had an assessment card -- a brand-new lead gets its first
    assessment (with the deck) via the normal sync_copper import flow, so
    re-queuing here would just duplicate work. The on-demand per-lead endpoint
    passes `require_existing_card=False` since a user explicitly asking to
    fetch a deck always wants the resulting re-score, regardless of whether
    an assessment already exists.
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

        if require_existing_card:
            existing_card = await db.execute(
                select(AssessmentCard.id).where(AssessmentCard.lead_id == lead.id).limit(1)
            )
            should_requeue = existing_card.scalar_one_or_none() is not None
        else:
            should_requeue = True

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

        matched, unmatched, failed, requeued = 0, 0, 0, 0
        unmatched_files: list[dict] = []
        for drive_file in drive_files:
            match = find_lead_match(drive_file["name"], remaining_leads)
            if not match.lead:
                unmatched += 1
                candidates = [
                    {"company_name": c.company_name, "score": round(c.score, 2)}
                    for c in match.candidates
                ]
                unmatched_files.append({"name": drive_file["name"], "candidates": candidates})
                if candidates:
                    closest = ", ".join(f"{c['company_name']!r} {c['score']:.2f}" for c in candidates)
                    logger.warning(
                        "%r -> no confident match (closest: %s, threshold %.2f)",
                        drive_file["name"], closest, MATCH_THRESHOLD,
                    )
                else:
                    logger.warning(
                        "%r -> no confident match (no unmatched leads to compare)",
                        drive_file["name"],
                    )
                continue

            lead = match.lead
            remaining_leads.remove(lead)
            try:
                if await _ingest_from_drive(db, service, lead, drive_file):
                    requeued += 1
                matched += 1
            except Exception:
                # One bad file (download hiccup, corrupt PDF, etc.) must not
                # abort the whole sync -- the lead simply isn't marked as
                # matched here, so the next run's DB-driven remaining_leads
                # query picks it up again for a retry.
                failed += 1
                logger.exception(
                    "failed to ingest Drive file %r for lead %r; continuing with remaining files",
                    drive_file["name"], lead.company_name,
                )

    result = {
        "drive_files": len(drive_files),
        "matched": matched,
        "unmatched": unmatched,
        "failed": failed,
        "reassessments_queued": requeued,
        "unmatched_files": unmatched_files,
    }
    print(f"[sync_pitch_decks] {result}")
    return result


_MAX_REPORTED_FILES = 5


async def sync_lead_pitch_deck(db: AsyncSession, lead: Lead, *, force: bool = False) -> dict:
    """On-demand Drive fetch+match+attach for a SINGLE lead.

    Powers the "Fetch pitch deck" button (POST /leads/{id}/sync-pitch-deck):
    unlike the scheduled sweep in _run(), this always runs synchronously
    inside the request so the caller gets a structured diagnostic back
    instead of having to guess why nothing happened. Every failure branch is
    caught and turned into a `reason` string rather than propagating (the
    caller must never see a bare 500 here).

    Reuses _drive_service/_list_pdfs_in_folder/find_lead_match/
    extract_text_from_pdf/_ingest_from_drive -- the exact same pieces the
    scheduled sweep uses -- so the two paths can't drift apart.
    """
    diagnostic = {
        "configured": bool(settings.google_service_account_json),
        "folder_readable": False,
        "files_in_folder": 0,
        "matched_file": None,
        "closest_candidates": [],
        "attached": False,
        "extracted_chars": 0,
        "garbled": False,
        "reassessment_queued": False,
        "reason": "",
    }

    if lead.pitch_deck_drive_id and not force:
        diagnostic.update(
            attached=True,
            matched_file=lead.pitch_deck_filename,
            extracted_chars=len(lead.pitch_deck_text or ""),
            reason=(
                f"{lead.pitch_deck_filename!r} is already attached to this lead. "
                "Pass force=true to re-fetch it from Drive."
            ),
        )
        return diagnostic

    if not diagnostic["configured"]:
        diagnostic["reason"] = (
            "Google service account isn't configured (GOOGLE_SERVICE_ACCOUNT_JSON is unset) "
            "-- deck fetching is disabled until that secret is set."
        )
        return diagnostic

    try:
        service = _drive_service()
        drive_files = _list_pdfs_in_folder(service, settings.drive_pitch_deck_folder_id)
    except Exception as exc:
        diagnostic["reason"] = f"Service account can't read the folder: {exc!r}"
        return diagnostic

    diagnostic["folder_readable"] = True
    diagnostic["files_in_folder"] = len(drive_files)

    # Invert the usual "one filename vs many leads" matching call into "one
    # lead vs many filenames" by calling find_lead_match once per file with
    # this single lead as the only candidate -- reuses the exact same
    # normalization/threshold/exact-match logic the scheduled sweep relies on.
    scored = []
    for drive_file in drive_files:
        match = find_lead_match(drive_file["name"], [lead])
        score = match.candidates[0].score if match.candidates else 0.0
        scored.append((drive_file, score, match.lead is not None))
    scored.sort(key=lambda t: t[1], reverse=True)

    matched_files = [f for f, _, is_match in scored if is_match]
    if not matched_files:
        diagnostic["closest_candidates"] = [f["name"] for f, _, _ in scored[:_MAX_REPORTED_FILES]]
        folder_listing = ", ".join(diagnostic["closest_candidates"]) or "(the folder is empty)"
        diagnostic["reason"] = (
            f"No file matching {lead.company_name!r} found; folder has: {folder_listing}"
        )
        return diagnostic

    # Multiple files independently clearing the bar against this one lead is
    # rare and out of scope here (see issue #44) -- take the closest.
    drive_file = matched_files[0]
    diagnostic["matched_file"] = drive_file["name"]

    try:
        requeued = await _ingest_from_drive(db, service, lead, drive_file, require_existing_card=False)
    except Exception as exc:
        diagnostic["reason"] = f"Found {drive_file['name']!r} but failed to download/extract it: {exc!r}"
        return diagnostic

    diagnostic["attached"] = True
    diagnostic["extracted_chars"] = len(lead.pitch_deck_text or "")
    diagnostic["garbled"] = not lead.pitch_deck_text
    diagnostic["reassessment_queued"] = requeued

    if lead.pitch_deck_text:
        diagnostic["reason"] = (
            f"Attached {drive_file['name']!r} ({diagnostic['extracted_chars']} chars) "
            "and queued a re-assessment."
        )
    else:
        diagnostic["reason"] = (
            f"Found and downloaded {drive_file['name']!r}, but text extraction was garbled "
            "or empty -- not stored for scoring. The file is still viewable via Drive."
        )
    return diagnostic


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
