"""
Delete stale duplicate AssessmentCard rows.

Keeps the newest card per lead_id (by created_at desc) and deletes the rest.
Use before applying the unique constraint migration.

Usage: python scripts/dedupe_assessment_cards.py [--dry-run]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.database import AsyncSessionLocal


async def run(dry_run: bool) -> None:
    async with AsyncSessionLocal() as db:
        # Find all (lead_id, card_id) pairs that are NOT the newest.
        r = await db.execute(text("""
            WITH ranked AS (
                SELECT id, lead_id,
                       ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY created_at DESC) AS rn
                FROM assessment_cards
            )
            SELECT id, lead_id FROM ranked WHERE rn > 1
        """))
        stale = r.fetchall()
        print(f"Stale duplicate cards to delete: {len(stale)}")
        if not stale:
            return
        if dry_run:
            for row in stale[:10]:
                print(f"  would delete card={row[0]} lead={row[1]}")
            if len(stale) > 10:
                print(f"  ... and {len(stale) - 10} more")
            return

        ids = [row[0] for row in stale]
        await db.execute(text("DELETE FROM assessment_cards WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()
        print(f"Deleted {len(stale)} duplicate cards.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
