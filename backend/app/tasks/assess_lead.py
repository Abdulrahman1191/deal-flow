from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import select, text

from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.models.assessment import AssessmentCard
from app.services import claude_agent, research
from app.services import copper_writer
from app.services.events import EVENT_ASSESSED, log_event
from app.tasks.celery_app import celery


def _mark_failed(lead_id: str, error: str) -> None:
    """Sync DB write to flip a stuck 'processing' lead to 'failed'.
    Called when retries are exhausted so the UI doesn't show a spinner forever."""
    from sqlalchemy import create_engine
    from app.config import settings

    # asyncpg takes ?ssl=require; psycopg2 wants ?sslmode=require (Neon).
    url = settings.database_url.replace("+asyncpg", "+psycopg2").replace("ssl=require", "sslmode=require")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE leads SET status='failed' WHERE id=:lid AND status IN ('processing','pending')"),
            {"lid": lead_id},
        )
    print(f"[assess_lead] marked lead {lead_id} as failed: {error}")


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def assess_lead_task(self, lead_id: str) -> dict:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run(lead_id))
        finally:
            loop.close()
    except MaxRetriesExceededError as exc:
        _mark_failed(lead_id, repr(exc))
        return {"lead_id": lead_id, "status": "failed", "error": "max retries exceeded"}
    except Exception as exc:
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            _mark_failed(lead_id, repr(exc))
            return {"lead_id": lead_id, "status": "failed", "error": repr(exc)}


async def _run(lead_id: str) -> dict:
    async with CelerySessionLocal() as db:
        result = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
        lead = result.scalar_one_or_none()
        if not lead:
            return {"error": "Lead not found"}

        lead.status = "processing"
        await db.commit()

        # If Copper didn't surface a company LinkedIn, try discovery:
        #   1) scrape the company's own website (most reliable)
        #   2) LLM verifier over Tavily search results (broader fallback)
        if not lead.company_linkedin_url:
            found = research.scrape_linkedin_from_website(lead.website)
            if not found and lead.company_name:
                found = research.find_linkedin_via_llm_search(
                    company_name=lead.company_name,
                    website=lead.website or "",
                    founder_names=lead.founder_names,
                    description=lead.description or "",
                    region=lead.region or "",
                )
            if found:
                lead.company_linkedin_url = found
                await db.commit()

        lead_data = {
            "company_name": lead.company_name,
            "website": lead.website,
            "description": lead.description,
            "stage": lead.stage,
            "region": lead.region,
            "founder_names": lead.founder_names,
            "linkedin_urls": lead.linkedin_urls,
            "company_linkedin_url": lead.company_linkedin_url,
            "pitch_deck_text": lead.pitch_deck_text,
        }

        research_data = research.research_company(lead_data)
        assessment_result = claude_agent.assess_lead(lead_data, research_data)

        # Upsert: update existing card if present, otherwise create one.
        existing = await db.execute(
            select(AssessmentCard).where(AssessmentCard.lead_id == lead.id)
            .order_by(AssessmentCard.created_at.desc()).limit(1)
        )
        card = existing.scalar_one_or_none()
        fields = dict(
            bucket=assessment_result["bucket"],
            confidence_score=assessment_result["confidence_score"],
            summary=assessment_result.get("summary"),
            positive_signals=assessment_result.get("positive_signals"),
            red_flags=assessment_result.get("red_flags"),
            data_gaps=assessment_result.get("data_gaps"),
            scoring_breakdown=assessment_result.get("scoring_breakdown"),
            draft_subject=assessment_result.get("draft_subject"),
            draft_body=assessment_result.get("draft_body"),
            draft_type=assessment_result.get("draft_type"),
            research_sources=assessment_result.get("research_sources"),
            research_data=research_data,  # preserve full Tavily input for training
            user_override=None,
            user_override_at=None,
            approved_at=None,
            sent_at=None,
        )
        if card:
            for k, v in fields.items():
                setattr(card, k, v)
        else:
            card = AssessmentCard(lead_id=lead.id, **fields)
            db.add(card)

        if lead.status != "archived":
            lead.status = "assessed"
        await log_event(
            db,
            lead.id,
            EVENT_ASSESSED,
            {"bucket": card.bucket, "confidence_score": card.confidence_score},
        )
        await db.commit()

        # Push bucket tag to Copper via outbox (F2). Best-effort: don't fail the task.
        if lead.copper_id:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            try:
                copper_writer.push_assessment(lead.copper_id, card.bucket, existing_tags)
            except Exception as exc:
                print(f"[assess_lead] outbox enqueue failed: {exc!r}")

        return {"lead_id": lead_id, "bucket": card.bucket, "confidence_score": card.confidence_score}
