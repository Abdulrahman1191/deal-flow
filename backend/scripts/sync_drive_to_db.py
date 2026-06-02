"""
sync_drive_to_db.py — one-shot backfill mapping Drive PDFs → DB leads.

You run this LOCALLY (not on the platform) once after uploading the 427
pitch decks to Drive. It:

  1. Opens an OAuth browser flow to Google (token cached at
     ~/.cache/raed-deal-flow/drive-token.json so subsequent runs are quiet)
  2. Lists every *.pdf in the configured Drive folder
  3. Fuzzy-matches each filename to a Lead.company_name (same logic the
     existing pitch_deck.match_filename_to_lead uses)
  4. Updates the matched lead's pitch_deck_drive_id

Re-run any time you add new decks to the Drive folder. Idempotent —
already-matched leads keep their existing Drive ID unless --force is set.

Usage:
  # Set up OAuth client (one time)
  pip install google-auth google-auth-oauthlib google-api-python-client

  # Place oauth-credentials.json from Google Cloud Console in ~/.config/raed-deal-flow/
  # (the script will tell you exactly where on first run)

  # Run it
  export DRIVE_PITCH_DECK_FOLDER_ID=<the Drive folder ID, e.g. 1aBcDe...>
  export DATABASE_URL=postgresql+asyncpg://...
  python scripts/sync_drive_to_db.py [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.services.pitch_deck import match_filename_to_lead

# Google API libraries — only imported when this script is actually invoked
# so the production runtime doesn't need them in requirements.txt.
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    sys.exit(
        "Missing Google API client libraries. Install them with:\n"
        "  pip install google-auth google-auth-oauthlib google-api-python-client"
    )


SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

CONFIG_DIR = Path.home() / ".config" / "raed-deal-flow"
CACHE_DIR = Path.home() / ".cache" / "raed-deal-flow"
OAUTH_CLIENT_FILE = CONFIG_DIR / "oauth-credentials.json"
TOKEN_FILE = CACHE_DIR / "drive-token.json"


def _drive_service():
    """Open OAuth flow on first run; cached token on subsequent runs."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not OAUTH_CLIENT_FILE.exists():
        raise SystemExit(
            f"OAuth client credentials not found at {OAUTH_CLIENT_FILE}.\n"
            "\n"
            "Set it up once:\n"
            "  1. Go to https://console.cloud.google.com/apis/credentials\n"
            "  2. Create OAuth client ID (type: Desktop app)\n"
            "  3. Download the JSON, save it to the path above\n"
            "  4. Re-run this script\n"
        )

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(OAUTH_CLIENT_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def _list_pdfs_in_folder(service, folder_id: str) -> list[dict]:
    """Paginated list of every PDF in the folder (non-recursive — flat layout)."""
    files: list[dict] = []
    page_token = None
    q = (
        f"'{folder_id}' in parents and "
        "mimeType='application/pdf' and trashed=false"
    )
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


async def run(folder_id: str, dry_run: bool, force: bool) -> None:
    print(f"[1/3] Listing PDFs in Drive folder {folder_id}…")
    svc = _drive_service()
    drive_files = _list_pdfs_in_folder(svc, folder_id)
    print(f"      found {len(drive_files)} PDFs")

    print("[2/3] Loading leads from DB…")
    async with AsyncSessionLocal() as db:
        all_leads = (await db.execute(select(Lead))).scalars().all()
        print(f"      {len(all_leads)} leads in DB")

        print("[3/3] Matching + writing…")
        hits, skipped, misses, force_overwrites = 0, 0, 0, 0
        for f in drive_files:
            name = f["name"]
            file_id = f["id"]
            lead = match_filename_to_lead(name, all_leads)
            if not lead:
                misses += 1
                continue
            if lead.pitch_deck_drive_id and lead.pitch_deck_drive_id == file_id:
                skipped += 1
                continue
            if lead.pitch_deck_drive_id and not force:
                # Different file already mapped — preserve unless --force
                skipped += 1
                continue
            if lead.pitch_deck_drive_id:
                force_overwrites += 1
            else:
                hits += 1
            if not dry_run:
                lead.pitch_deck_drive_id = file_id
                if not lead.pitch_deck_filename:
                    lead.pitch_deck_filename = name
        if not dry_run:
            await db.commit()

    print(
        f"\nDone. {hits} new mappings, {force_overwrites} overwritten (with --force), "
        f"{skipped} already up-to-date, {misses} Drive files matched no lead."
    )
    if dry_run:
        print("(dry run — no DB writes)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--folder-id",
        default=os.getenv("DRIVE_PITCH_DECK_FOLDER_ID"),
        help="Drive folder ID (or DRIVE_PITCH_DECK_FOLDER_ID env var)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing Drive IDs when a re-match is found",
    )
    args = parser.parse_args()

    if not args.folder_id:
        raise SystemExit(
            "Need --folder-id or DRIVE_PITCH_DECK_FOLDER_ID env var. "
            "Get this from the Drive folder URL: "
            "https://drive.google.com/drive/folders/<THIS_PART>"
        )

    asyncio.run(run(args.folder_id, args.dry_run, args.force))


if __name__ == "__main__":
    main()
