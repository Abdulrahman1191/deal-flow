"""
Watch the pitch deck inbox folder and ingest any new PDFs as they appear.

Usage:
  python scripts/watch_pitch_decks.py
  nohup python scripts/watch_pitch_decks.py > /tmp/pdwatch.log 2>&1 &

Polls every 30s. Idempotent: a file already-ingested for its matching lead is
skipped on subsequent passes. Files that didn't match any lead are retried each
pass (helpful when a lead syncs later from Copper).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.services.pitch_deck import ingest_pdf, match_filename_to_lead

POLL_INTERVAL_S = 30


async def sweep_once(folder: Path) -> tuple:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Lead))
        leads = result.scalars().all()
        new_ingest = 0
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() != ".pdf":
                continue
            lead = match_filename_to_lead(path.name, leads)
            if not lead:
                continue
            if lead.pitch_deck_filename == path.name and lead.pitch_deck_text:
                continue
            if await ingest_pdf(db, lead, path):
                new_ingest += 1
                print(f"  ingested {path.name} -> {lead.company_name}", flush=True)
        return new_ingest


async def main() -> None:
    folder = Path(settings.pitch_deck_inbox).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    print(f"[watch_pitch_decks] watching {folder} every {POLL_INTERVAL_S}s", flush=True)
    while True:
        try:
            n = await sweep_once(folder)
            if n:
                print(f"[watch_pitch_decks] sweep ingested {n} file(s)", flush=True)
        except Exception as exc:
            print(f"[watch_pitch_decks] sweep error: {exc!r}", flush=True)
        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
