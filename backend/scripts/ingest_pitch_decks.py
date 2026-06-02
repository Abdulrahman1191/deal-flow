"""
Bulk ingest pitch decks from a local folder.

Usage:
  python scripts/ingest_pitch_decks.py                      # uses settings.pitch_deck_inbox
  python scripts/ingest_pitch_decks.py --folder /some/path

For each *.pdf in the folder, fuzzy-match the filename to a Lead.company_name,
extract text, store on the lead, and queue a re-assessment.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.services.pitch_deck import ingest_pdf, match_filename_to_lead


async def run(folder: Path) -> None:
    if not folder.exists():
        raise SystemExit(f"Folder does not exist: {folder}")

    pdfs = sorted([p for p in folder.iterdir() if p.suffix.lower() == ".pdf"])
    if not pdfs:
        raise SystemExit(f"No .pdf files in {folder}")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Lead))
        leads = result.scalars().all()

        hits, misses, skipped = 0, 0, 0
        for path in pdfs:
            lead = match_filename_to_lead(path.name, leads)
            if not lead:
                print(f"  miss {path.name}")
                misses += 1
                continue

            # Idempotency: skip if we've already ingested this exact filename
            if lead.pitch_deck_filename == path.name and lead.pitch_deck_text:
                print(f"  skip {path.name} -> {lead.company_name} (already ingested)")
                skipped += 1
                continue

            ok = await ingest_pdf(db, lead, path)
            if ok:
                print(f"  HIT  {path.name} -> {lead.company_name}")
                hits += 1
            else:
                print(f"  fail {path.name} (parse error)")
                misses += 1

        print(f"\nDone. {hits} ingested, {skipped} already up-to-date, {misses} unmatched/failed.")
        if hits:
            print(f"{hits} re-assessments queued; watch the Celery worker log.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default=settings.pitch_deck_inbox)
    args = parser.parse_args()
    asyncio.run(run(Path(args.folder).expanduser()))


if __name__ == "__main__":
    main()
