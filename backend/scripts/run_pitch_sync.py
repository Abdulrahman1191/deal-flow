"""
Run the pitch-deck Drive sync once, on demand, plus a read-only diagnostic.

The scheduled Celery task (app.tasks.sync_pitch_decks.sync_pitch_decks_task)
only runs every 30 minutes (see celery_app.py beat schedule). This script
calls the exact same underlying function so a successful manual run here
proves the scheduled task will work too, without waiting for the next cycle.

Usage (from backend/):
  python scripts/run_pitch_sync.py            # run the sync once
  python scripts/run_pitch_sync.py --check     # read-only diagnostic, mutates nothing

Reads DATABASE_URL and GOOGLE_SERVICE_ACCOUNT_JSON from env, same as the task.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.tasks.sync_pitch_decks import _drive_service, _list_pdfs_in_folder, _run

MAX_CHECK_FILENAMES = 10


def format_check_report(email: str, folder_id: str, files: list[dict], max_names: int = MAX_CHECK_FILENAMES) -> str:
    """Render the --check diagnostic: SA email, folder readability, file preview."""
    lines = [
        f"Service account: {email}",
        f"Drive folder ({folder_id}): OK, {len(files)} file(s) visible",
    ]
    for f in files[:max_names]:
        lines.append(f"  - {f['name']}")
    if len(files) > max_names:
        lines.append(f"  ... and {len(files) - max_names} more")
    return "\n".join(lines)


def format_sync_summary(result: dict) -> str:
    """Render the post-sync report from whatever sync_pitch_decks._run() returns.

    `matched` doubles as "decks newly attached": _run() always records
    pitch_deck_drive_id/filename on a matched lead, even when text extraction
    later fails, so matching and attaching are the same count in this pipeline.
    """
    return (
        f"Drive files seen:      {result['drive_files']}\n"
        f"Leads matched:         {result['matched']}\n"
        f"Decks newly attached:  {result['matched']}\n"
        f"Reassessments queued:  {result['reassessments_queued']}\n"
        f"Unmatched files:       {result['unmatched']}"
    )


def _diagnose_drive_error(exc: Exception, sa_email: str, folder_id: str) -> str:
    """Best-effort one-line cause for a Drive API failure during --check."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        HttpError = ()  # google-api-python-client always ships alongside this script

    if isinstance(exc, HttpError):
        status = exc.resp.status if getattr(exc, "resp", None) is not None else None
        if status == 404:
            return (
                f"Folder {folder_id!r} not found, or not shared with the service "
                f"account ({sa_email}) -- share the Drive folder with that email."
            )
        if status == 403:
            return (
                "Permission denied -- either the Drive API isn't enabled on the "
                f"GCP project, or the folder isn't shared with {sa_email}."
            )
        return f"Drive API error (HTTP {status}): {exc}"
    return f"{type(exc).__name__}: {exc}"


def run_check() -> int:
    try:
        info = json.loads(settings.google_service_account_json)
    except json.JSONDecodeError:
        print("GOOGLE_SERVICE_ACCOUNT_JSON is set but is not valid JSON.")
        return 1

    if info.get("type") != "service_account":
        print(
            f"GOOGLE_SERVICE_ACCOUNT_JSON has type={info.get('type')!r}, expected "
            "'service_account' -- looks like the wrong key type (e.g. an OAuth "
            "client secret) was pasted in."
        )
        return 1

    sa_email = info.get("client_email", "<unknown -- missing client_email in key JSON>")
    folder_id = settings.drive_pitch_deck_folder_id
    print(f"Service account: {sa_email}")
    print(f"Checking Drive folder {folder_id!r}...")

    try:
        service = _drive_service()
        files = _list_pdfs_in_folder(service, folder_id)
    except Exception as exc:
        print(f"FAILED: {_diagnose_drive_error(exc, sa_email, folder_id)}")
        return 1

    print(format_check_report(sa_email, folder_id, files))
    return 0


def run_sync() -> int:
    result = asyncio.run(_run())
    print(format_sync_summary(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only diagnostic: verify Drive access without mutating anything.",
    )
    args = parser.parse_args(argv)

    if not settings.google_service_account_json:
        print("GOOGLE_SERVICE_ACCOUNT_JSON is not set -- nothing to run.")
        return 1

    return run_check() if args.check else run_sync()


if __name__ == "__main__":
    sys.exit(main())
