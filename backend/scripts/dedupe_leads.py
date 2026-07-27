"""
De-duplicate leads by (owner_email, normalized company name) (manual CLI).

Thin wrapper over app.services.dedup.dedupe_leads — the same logic the scheduled
Celery task runs automatically (app/tasks/dedupe_leads.py). Use this for an
ad-hoc dry-run/apply; the worker also runs it daily on its own.

Grouping is scoped per-owner: the same company assigned to two different
partners in Copper is two distinct leads, never merged together. A merge never
loses data -- if only the archived row(s) had a pitch deck or a completed
assessment, the canonical row inherits it before the others are archived.

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

        print("Per-owner duplicates to archive:")
        for owner, count in sorted(report["per_owner"].items()):
            print(f"  {owner}: {count}")
        print()

        for g in report["detail"]:
            tags = []
            if g["deck_inherited"]:
                tags.append("deck inherited")
            if g["assessment_inherited"]:
                tags.append("assessment inherited")
            tag_suffix = f"  [{', '.join(tags)}]" if tags else ""
            print(f"[{g['owner_email']} / {g['name']}]  "
                  f"keep {g['keep'][:8]} (copper_id={g['keep_copper_id']}) — "
                  f"archive {len(g['archive'])}: "
                  + ", ".join(f"{s[:8]} (copper_id={c})"
                              for s, c in zip(g["archive"], g["archive_copper_ids"]))
                  + tag_suffix)

        if COMMIT:
            print(f"\nCOMMITTED — archived {report['archived']} duplicate(s).")
        else:
            print("\nDRY RUN — re-run with --commit to apply.")


if __name__ == "__main__":
    asyncio.run(main())
