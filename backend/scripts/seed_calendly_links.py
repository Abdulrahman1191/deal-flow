"""
Seed known per-user Calendly links (issue #84).

Idempotent: sets users.calendly_url for the emails listed below, overwriting
any existing value so re-running after an update in this file re-syncs it.
Users not listed (or not yet provisioned) are left untouched -- draft
generation falls back to the default link/name when calendly_url is null.

Usage: python scripts/seed_calendly_links.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User

# Yomna's and Almuhammed's links are not yet known -- omitted on purpose.
KNOWN_CALENDLY_LINKS = {
    "abdulrahman@raed.vc": "https://calendly.com/abdulrahman-raed/30min",
    "uday@raed.vc": "https://calendly.com/udayrvc/30min",
    "waleed@raed.vc": "https://calendly.com/waleed-raed/pl",
}


async def run() -> None:
    async with AsyncSessionLocal() as db:
        updated, missing = 0, []
        for email, calendly_url in KNOWN_CALENDLY_LINKS.items():
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            if not user:
                missing.append(email)
                continue
            user.calendly_url = calendly_url
            updated += 1

        await db.commit()
        print(f"Seeded {updated} Calendly link(s).")
        if missing:
            print(f"Skipped (no user provisioned yet): {', '.join(missing)}")


if __name__ == "__main__":
    asyncio.run(run())
