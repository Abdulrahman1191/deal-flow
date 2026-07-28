"""
Re-extract text (with OCR fallback) for leads whose pitch deck is already
attached via Drive (pitch_deck_drive_id set) but never yielded usable text
(pitch_deck_text empty) -- image-only/scanned decks, or Arabic decks whose
extraction failed before the OCR fallback existed (issue #97).

Re-downloads each candidate's deck from Drive by its stored file id and
re-runs extract_text_from_pdf (PyMuPDF -> pypdf -> OCR; see
app/services/pitch_deck.py). Any lead that now yields text gets it stored and
a re-assessment queued, so leads stuck in `awaiting_deck` unstick without
waiting for a re-upload.

Firm-wide, all owners, idempotent: a lead that already has pitch_deck_text
never matches the candidate query, so a second run is a no-op for it.

Dry-run by default: lists candidates, makes NO downloads/writes. --commit
re-downloads, re-extracts, and (for leads that now yield text) commits +
queues assess_lead_task.

Usage (from backend/):
  python scripts/reextract_pitch_decks.py            # dry run
  python scripts/reextract_pitch_decks.py --commit

Reads DATABASE_URL and GOOGLE_SERVICE_ACCOUNT_JSON from the environment (same
as the app/worker). No-ops gracefully if the latter is unset -- there's
nothing to re-download from Drive without it.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.services.pitch_deck import extract_text_from_pdf


# --- Pure planning step (no network/DB writes) ------------------------------

def plan_reextract(leads: Iterable[Lead]) -> dict:
    """leads: candidate rows already filtered by the DB query below (a Drive
    deck attached, but no usable text yet). Returns the plan for reporting."""
    rows = [
        {
            "id": str(lead.id),
            "company_name": lead.company_name,
            "owner_email": lead.owner_email,
            "drive_id": lead.pitch_deck_drive_id,
            "filename": lead.pitch_deck_filename,
        }
        for lead in leads
    ]
    return {"leads": rows}


def render_plan(plan: dict) -> str:
    lines = [
        "Re-extract pitch-deck text via OCR fallback for stuck leads (all owners)",
        f"=== Candidates: {len(plan['leads'])} ===",
    ]
    for row in plan["leads"]:
        lines.append(f"  - {row['company_name']!r} (owner={row['owner_email']}, file={row['filename']!r})")
    return "\n".join(lines)


# --- DB access ---------------------------------------------------------------

async def _fetch_candidates(db: AsyncSession) -> list[Lead]:
    result = await db.execute(
        select(Lead).where(
            Lead.pitch_deck_drive_id.isnot(None),
            Lead.pitch_deck_drive_id != "",
            or_(Lead.pitch_deck_text.is_(None), Lead.pitch_deck_text == ""),
        )
    )
    return list(result.scalars().all())


# --- Apply step (network + DB writes) ---------------------------------------

async def apply_reextract(db: AsyncSession, service, leads: list[Lead]) -> dict:
    """Re-download + re-extract each candidate. Per-lead failures (bad file,
    Drive hiccup, corrupt PDF) are caught and counted rather than aborting the
    rest of the run -- one bad deck must not block the other ~26."""
    from app.tasks.sync_pitch_decks import _download_pdf

    unstuck, still_empty, failed = 0, 0, 0
    for lead in leads:
        filename = lead.pitch_deck_filename or f"{lead.pitch_deck_drive_id}.pdf"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                pdf_path = Path(tmp_dir) / filename
                _download_pdf(service, lead.pitch_deck_drive_id, pdf_path)
                text = extract_text_from_pdf(pdf_path)
        except Exception as exc:
            failed += 1
            print(f"[reextract] lead={lead.id} company={lead.company_name!r}: failed ({exc!r})")
            continue

        if not text:
            still_empty += 1
            print(f"[reextract] lead={lead.id} company={lead.company_name!r}: still no usable text")
            continue

        lead.pitch_deck_text = text
        lead.pitch_deck_ingested_at = datetime.now(timezone.utc)
        await db.commit()

        # Re-queued after commit lands, matching _ingest_from_drive's ordering
        # in app/tasks/sync_pitch_decks.py -- assess_lead_task re-fetches the
        # lead at task start, so queuing before commit risks a worker picking
        # it up with pitch_deck_text still NULL.
        from app.tasks.assess_lead import assess_lead_task
        assess_lead_task.delay(str(lead.id))

        unstuck += 1
        print(
            f"[reextract] lead={lead.id} company={lead.company_name!r}: "
            f"unstuck ({len(text)} chars), re-assessment queued"
        )

    return {"unstuck": unstuck, "still_empty": still_empty, "failed": failed}


# --- CLI ----------------------------------------------------------------------

async def _run(commit: bool) -> int:
    if not settings.google_service_account_json:
        print(
            "GOOGLE_SERVICE_ACCOUNT_JSON not set; skipping "
            "(nothing can be re-downloaded from Drive without it)."
        )
        return 0

    async with AsyncSessionLocal() as db:
        leads = await _fetch_candidates(db)
        plan = plan_reextract(leads)
        print(render_plan(plan))

        if not commit:
            print(
                f"\nDRY RUN -- {len(plan['leads'])} lead(s) would be re-downloaded and "
                "re-extracted. Re-run with --commit to apply."
            )
            return 0

        if not plan["leads"]:
            print("\nNothing to do.")
            return 0

        from app.tasks.sync_pitch_decks import _drive_service
        service = _drive_service()
        result = await apply_reextract(db, service, leads)
        print(f"\nDone. {result}")
        return 0


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true",
                         help="Apply the writes. Without this flag, dry-run only (no downloads/writes).")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.commit))


if __name__ == "__main__":
    sys.exit(main())
