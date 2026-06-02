"""
Reset the TEST env (raedventures_test DB + Redis DB 1):
- Delete all leads + assessment cards from the test database.
- Purge the test Celery queue + stale task results.
- Optionally re-trigger a sync to repopulate with TEST_LEAD_LIMIT leads.

Run from backend/ dir: `python scripts/reset_test_env.py [--no-resync]`
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis
from sqlalchemy import delete

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead


async def wipe_db() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AssessmentCard))
        await db.execute(delete(Lead))
        await db.commit()


def wipe_redis() -> None:
    r = redis.Redis.from_url(settings.redis_url)
    r.delete("celery")
    for k in r.keys("celery-task-meta-*"):
        r.delete(k)


def main() -> None:
    db_name = settings.database_url.rsplit("/", 1)[-1]
    if db_name != "raedventures_test":
        raise SystemExit(
            f"Refusing to reset: DATABASE_URL points at '{db_name}', not 'raedventures_test'. "
            "Run this only inside the test env."
        )

    print(f"Wiping {db_name}...")
    asyncio.run(wipe_db())
    print(f"Purging Redis ({settings.redis_url})...")
    wipe_redis()
    print("Done.")

    if "--no-resync" not in sys.argv:
        from app.tasks.sync_copper import sync_copper_leads_task
        r = sync_copper_leads_task.delay()
        print(f"Queued fresh sync (lead cap={settings.test_lead_limit}): {r.id}")


if __name__ == "__main__":
    main()
