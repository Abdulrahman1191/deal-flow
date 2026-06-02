from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.briefing import DailyBriefing
from app.services import claude_agent, research
from app.tasks.celery_app import celery


@celery.task(bind=True, max_retries=2, default_retry_delay=300)
def generate_briefing_task(self) -> dict:
    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run() -> dict:
    today = date.today()
    date_str = today.isoformat()

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(DailyBriefing).where(DailyBriefing.date == today))
        if existing.scalar_one_or_none():
            return {"status": "already_exists", "date": date_str}

        research_data = research.research_briefing_topics(date_str)
        briefing_result = claude_agent.generate_briefing(date_str, research_data)

        briefing = DailyBriefing(
            date=today,
            top_themes=briefing_result["top_themes"],
            deep_dives=briefing_result["deep_dives"],
            raw_research=str(research_data),
        )
        db.add(briefing)
        await db.commit()

        return {"status": "generated", "date": date_str}
