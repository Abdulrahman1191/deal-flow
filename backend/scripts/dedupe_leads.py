"""
De-duplicate leads by normalized company name (manual CLI).

Thin wrapper over app.services.dedup.dedupe_leads — the same logic the scheduled
Celery task runs automatically (app/tasks/dedupe_leads.py). Use this for an
ad-hoc dry-run/apply; the worker also runs it daily on its own.

Usage (writes only with --commit):
  DATABASE_URL=...neon...?ssl=require /tmp/eval-venv/bin/python scripts/dedupe_leads.py            # dry run
  DATABASE_URL=...neon...?ssl=require /tmp/eval-venv/bin/python scripts/dedupe_leads.py --commit   # apply
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import AsyncSessionLocal
from app.services.dedup import dedupe_leads

COMMIT = "--commit" in sys.argv


async def main():
    async with AsyncSessionLocal() as db:
        report = await dedupe_leads(db, commit=COMMIT)
        print(f"== Lead de-dup ({'COMMIT' if COMMIT else 'DRY RUN'}) ==")
        print(f"{report['active']} active leads | {report['groups']} duplicate-name groups "
              f"| {report['to_archive']} leads to archive\n")
        for g in report["detail"]:
            print(f"[{g['name']}]  keep {g['keep'][:8]} — archive {len(g['archive'])}: "
                  + ", ".join(s[:8] for s in g["archive"]))
        if COMMIT:
            print(f"\nCOMMITTED — archived {report['archived']} duplicate(s).")
        else:
            print("\nDRY RUN — re-run with --commit to apply.")


if __name__ == "__main__":
    asyncio.run(main())
