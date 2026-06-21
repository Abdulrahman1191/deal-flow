from __future__ import annotations
import asyncio
from datetime import date

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.briefing import DailyBriefing
from app.models.user import User
from app.services import claude_agent, research
from app.tasks.celery_app import celery


def _generate_content(date_str: str) -> tuple[dict, str]:
    """Run research + the LLM to build today's briefing content (owner-agnostic;
    briefings are market research, not lead-derived)."""
    research_data = research.research_briefing_topics(date_str)
    briefing_result = claude_agent.generate_briefing(date_str, research_data)
    return briefing_result, str(research_data)


async def _insert_if_absent(db, today, owner_email, content, raw) -> bool:
    existing = await db.execute(
        select(DailyBriefing).where(
            DailyBriefing.date == today, DailyBriefing.owner_email == owner_email
        )
    )
    if existing.scalar_one_or_none():
        return False
    db.add(DailyBriefing(
        date=today, owner_email=owner_email,
        top_themes=content["top_themes"], deep_dives=content["deep_dives"],
        raw_research=raw,
    ))
    return True


@celery.task(bind=True, max_retries=2, default_retry_delay=300)
def generate_briefing_task(self, owner_email: str) -> dict:
    """Generate today's briefing for a single user (on-demand)."""
    try:
        return asyncio.run(_run_one(owner_email))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_one(owner_email: str) -> dict:
    owner_email = (owner_email or "").strip().lower()
    today = date.today()
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(DailyBriefing).where(
                DailyBriefing.date == today, DailyBriefing.owner_email == owner_email
            )
        )
        if existing.scalar_one_or_none():
            return {"status": "already_exists", "date": today.isoformat(), "owner": owner_email}
        content, raw = _generate_content(today.isoformat())
        await _insert_if_absent(db, today, owner_email, content, raw)
        await db.commit()
        return {"status": "generated", "date": today.isoformat(), "owner": owner_email}


@celery.task(bind=True, max_retries=2, default_retry_delay=300)
def generate_all_briefings_task(self) -> dict:
    """Beat task: generate today's briefing once and fan it out to every user."""
    try:
        return asyncio.run(_run_all())
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_all() -> dict:
    today = date.today()
    async with AsyncSessionLocal() as db:
        users = (await db.execute(select(User).where(User.is_active == True))).scalars().all()  # noqa: E712
        if not users:
            return {"status": "no_users"}
        # Generate the (owner-agnostic) content once, then store a copy per user.
        content, raw = _generate_content(today.isoformat())
        created = 0
        for u in users:
            if await _insert_if_absent(db, today, u.email, content, raw):
                created += 1
        await db.commit()
        return {"status": "generated", "date": today.isoformat(), "created": created}
