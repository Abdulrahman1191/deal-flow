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

A second, independent sweep in this module (sync_copper_pitch_deck_links_task
/ _run_copper_pitch_deck_links) covers a different channel: a Google Drive
link pasted into Copper's own "Pitch Deck" URL custom field
(COPPER_CF_PITCH_DECK_URL_ID). Copper's file attachments aren't downloadable
via its API, so that URL field is the sanctioned way to attach a deck from
inside Copper. It reuses the same _drive_service/_download_pdf/
_ingest_from_drive plumbing as the folder sweep above so the two paths can't
drift apart.
"""
import asyncio
import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import CelerySessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services.copper_service import fetch_lead_by_id
from app.services.pitch_deck import (
    MATCH_THRESHOLD,
    extract_text_from_pdf,
    find_lead_match,
    verify_match_candidates,
)
from app.tasks.celery_app import celery

logger = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Service account used by _drive_service() — surfaced in log lines so a
# maintainer knows exactly which principal to share a file with.
DECK_READER_SA_EMAIL = "deck-reader@starlit-hulling-489209-j5.iam.gserviceaccount.com"

_DRIVE_LINK_HOSTS = ("drive.google.com", "docs.google.com")


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


def _download_and_extract(service, drive_file: dict) -> str:
    """Download a Drive PDF to a temp file and extract its text.

    Shared by the match-verification tier (which needs the deck's content
    BEFORE deciding whether to attach) and _ingest_from_drive (which needs it
    to store on the lead) -- so a deck is downloaded/extracted at most once
    per file even when verification consumes it first.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_path = Path(tmp_dir) / drive_file["name"]
        _download_pdf(service, drive_file["id"], pdf_path)
        return extract_text_from_pdf(pdf_path)


def _parse_drive_file_id(url: Optional[str]) -> Optional[str]:
    """Extract a Drive file id from the common link shapes:
      - https://drive.google.com/file/d/<ID>/view?usp=sharing
      - https://drive.google.com/open?id=<ID>
      - https://docs.google.com/presentation/d/<ID>/edit
    Returns None for anything else, including non-Drive URLs and blanks --
    the service account can only fetch Drive files, not arbitrary URLs.
    """
    if not url or not url.strip():
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if not any(host == h or host.endswith(f".{h}") for h in _DRIVE_LINK_HOSTS):
        return None
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)
    query_id = parse_qs(parsed.query).get("id")
    return query_id[0] if query_id else None


def _pitch_deck_url_field(raw_copper_data: Optional[dict]) -> Optional[str]:
    """Reads the Copper "Pitch Deck" URL custom field (COPPER_CF_PITCH_DECK_URL_ID)
    out of a raw Copper lead dict's custom_fields list."""
    if not raw_copper_data:
        return None
    for cf in raw_copper_data.get("custom_fields") or []:
        if cf.get("custom_field_definition_id") == settings.copper_cf_pitch_deck_url_id:
            value = cf.get("value")
            return value.strip() if isinstance(value, str) and value.strip() else None
    return None


def _is_drive_permission_error(exc: Exception) -> bool:
    """True for a Drive API error caused by the service account not having
    access to the file (403) or the file not resolving for it at all (404 --
    Drive returns 404 rather than 403 for files an unauthenticated-for
    principal can't see). Import is lazy to match the rest of this module's
    optional google-api-client dependency."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        return False
    if not isinstance(exc, HttpError):
        return False
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in (403, 404)


async def _ingest_from_drive(
    db: AsyncSession,
    service,
    lead: Lead,
    drive_file: dict,
    *,
    require_existing_card: bool = True,
    deck_text: Optional[str] = None,
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

    `deck_text` lets a caller that already downloaded+extracted this file for
    match verification (issue #74) pass the text through instead of
    re-downloading it here.
    """
    text = _download_and_extract(service, drive_file) if deck_text is None else deck_text

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


async def _ingest_from_copper_link(db: AsyncSession, service, lead: Lead) -> str:
    """Try to attach the deck linked in this lead's Copper "Pitch Deck" URL
    field. Returns one of: 'ingested', 'skipped_non_drive', 'no_access',
    'no_link', 'failed'. Never raises -- every failure branch is caught and
    turned into an outcome string so one lead's bad link/permissions can't
    abort the rest of the batch.
    """
    raw_copper_data = None
    if lead.copper_id:
        try:
            raw_copper_data = fetch_lead_by_id(lead.copper_id)
        except Exception as exc:
            logger.warning(
                "lead=%s copper_id=%s: fresh Copper read failed (%r); falling back to cached raw_copper_data",
                lead.id, lead.copper_id, exc,
            )
    if not raw_copper_data:
        raw_copper_data = lead.raw_copper_data

    url = _pitch_deck_url_field(raw_copper_data)
    if not url:
        return "no_link"

    file_id = _parse_drive_file_id(url)
    if not file_id:
        logger.info(
            "lead=%s copper_id=%s: Pitch Deck field %r isn't a Drive link; skipping "
            "(the service account can only fetch Drive files, not arbitrary URLs)",
            lead.id, lead.copper_id, url,
        )
        return "skipped_non_drive"

    try:
        meta = service.files().get(fileId=file_id, fields="id, name").execute()
        drive_file = {"id": file_id, "name": meta.get("name") or f"{file_id}.pdf"}
        await _ingest_from_drive(db, service, lead, drive_file, require_existing_card=False)
    except Exception as exc:
        if _is_drive_permission_error(exc):
            logger.warning(
                "SA lacks access to %s; share the file/folder with %s", file_id, DECK_READER_SA_EMAIL,
            )
            return "no_access"
        logger.exception(
            "lead=%s copper_id=%s: failed to ingest Drive file %s linked from Copper's Pitch Deck field",
            lead.id, lead.copper_id, file_id,
        )
        return "failed"

    return "ingested"


async def _run_copper_pitch_deck_links() -> dict:
    """Sweep deckless leads firm-wide (all owners) for a Drive link pasted
    into Copper's "Pitch Deck" URL field, and ingest it. Caller
    (sync_copper_pitch_deck_links_task) gates on
    settings.copper_cf_pitch_deck_url_id being configured.
    """
    service = _drive_service()
    outcomes = {"ingested": 0, "skipped_non_drive": 0, "no_access": 0, "no_link": 0, "failed": 0}

    async with CelerySessionLocal() as db:
        result = await db.execute(
            select(Lead).where(
                or_(Lead.pitch_deck_text.is_(None), Lead.pitch_deck_text == ""),
                Lead.status.notin_(["archived", "approved"]),
            )
        )
        leads = result.scalars().all()

        for lead in leads:
            if lead.pitch_deck_drive_id:
                # Idempotent: already attached (e.g. by the Drive-folder sweep
                # or a prior run of this same sweep).
                continue
            try:
                outcome = await _ingest_from_copper_link(db, service, lead)
            except Exception:
                outcome = "failed"
                logger.exception(
                    "lead=%s copper_id=%s: unexpected error during Copper-link ingestion",
                    lead.id, lead.copper_id,
                )
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            print(f"[sync_pitch_decks][copper_link] lead={lead.id} copper_id={lead.copper_id} outcome={outcome}")

    result = {"leads_checked": len(leads), **outcomes}
    print(f"[sync_pitch_decks][copper_link] {result}")
    return result


async def _run() -> dict:
    service = _drive_service()
    drive_files = _list_pdfs_in_folder(service, settings.drive_pitch_deck_folder_id)

    async with CelerySessionLocal() as db:
        all_leads = (
            (await db.execute(select(Lead).where(Lead.status != "archived"))).scalars().all()
        )
        # Idempotency: a lead that already has a Drive-matched deck is never
        # re-matched, so a re-run with no new files changes nothing. Leads
        # ingested via the local scripts/ingest_pitch_decks.py flow have
        # pitch_deck_text set but no pitch_deck_drive_id ("on file, sync
        # pending" — see LeadCard.tsx) — exclude those too, since they
        # already have a deck and re-ingesting would overwrite it and queue
        # a spurious re-assessment. Archived leads (e.g. dedup losers) are
        # excluded up front too: they're deckless duplicates of an active
        # lead, and letting them into the candidate pool alongside their
        # active twin makes every match ambiguous, permanently blocking
        # auto-attach for that company (see dedup.py). Filtered again here
        # (not just in the query above) so callers that hand _run() an
        # already-fetched lead list -- e.g. tests -- get the same guarantee.
        remaining_leads = [
            l
            for l in all_leads
            if not l.pitch_deck_drive_id and not l.pitch_deck_text and l.status != "archived"
        ]

        matched, unmatched, failed, requeued = 0, 0, 0, 0
        unmatched_files: list[dict] = []
        for drive_file in drive_files:
            match = find_lead_match(drive_file["name"], remaining_leads)
            lead = match.lead
            # deck_text set here is reused by _ingest_from_drive below, so a
            # verified file's content isn't downloaded/extracted twice.
            deck_text: Optional[str] = None

            if lead is None and settings.deck_match_verify_enabled and match.needs_verification:
                try:
                    deck_text = _download_and_extract(service, drive_file)
                except Exception:
                    # A download hiccup during verification must not abort the
                    # sweep -- fall through and treat this file as unmatched;
                    # the next run's DB-driven remaining_leads query retries it.
                    logger.exception(
                        "failed to download %r for match verification; leaving unmatched",
                        drive_file["name"],
                    )
                    deck_text = None
                if deck_text:
                    lead = verify_match_candidates(match.needs_verification, deck_text)
                if lead is None:
                    deck_text = None  # nothing to reuse -- verification didn't resolve a lead

            if lead is None:
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

            remaining_leads.remove(lead)
            try:
                if await _ingest_from_drive(db, service, lead, drive_file, deck_text=deck_text):
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
        scored.append((drive_file, score, match))
    scored.sort(key=lambda t: t[1], reverse=True)

    matched_files = [f for f, _, match in scored if match.lead is not None]

    # No high-confidence match -- try the verification tier (issue #74): for
    # each near-miss candidate (best score first), download+extract its text
    # and check it against this lead's description via one cheap LLM call.
    # Since `[lead]` is the only candidate passed to find_lead_match above,
    # `needs_verification` holds at most this one lead per file.
    verified_deck_text: Optional[str] = None
    if not matched_files and settings.deck_match_verify_enabled:
        for drive_file, _score, match in scored:
            if not match.needs_verification:
                continue
            try:
                text = _download_and_extract(service, drive_file)
            except Exception:
                continue
            if not text:
                continue
            if verify_match_candidates(match.needs_verification, text):
                matched_files = [drive_file]
                verified_deck_text = text
                break

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
        requeued = await _ingest_from_drive(
            db, service, lead, drive_file, require_existing_card=False, deck_text=verified_deck_text
        )
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


@celery.task(bind=True, max_retries=3, default_retry_delay=120)
def sync_copper_pitch_deck_links_task(self) -> dict:
    """Beat task: sweep deckless leads for a Drive link in Copper's own
    "Pitch Deck" URL field. No-ops until both GOOGLE_SERVICE_ACCOUNT_JSON and
    COPPER_CF_PITCH_DECK_URL_ID are configured."""
    if not settings.google_service_account_json:
        print("[sync_pitch_decks][copper_link] GOOGLE_SERVICE_ACCOUNT_JSON not set; skipping")
        return {"skipped": "GOOGLE_SERVICE_ACCOUNT_JSON not set"}
    if not settings.copper_cf_pitch_deck_url_id:
        print("[sync_pitch_decks][copper_link] COPPER_CF_PITCH_DECK_URL_ID not set; skipping")
        return {"skipped": "COPPER_CF_PITCH_DECK_URL_ID not set"}
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run_copper_pitch_deck_links())
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(exc=exc)
