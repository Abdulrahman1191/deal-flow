from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone

from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded
from sqlalchemy import select, text

from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.models.assessment import AssessmentCard
from app.models.user import User
from app.services import claude_agent, research
from app.services import copper_writer
from app.services.events import EVENT_ASSESSED, EVENT_AWAITING_DECK, log_event
from app.tasks.celery_app import celery

# Bounds the worker-crash-loop (issue #129): acks_late + task_reject_on_worker_lost
# redelivers a task that crashes the worker forever, bypassing Celery's own
# max_retries (worker-lost redeliveries never touch self.request.retries). Once
# a lead's assessment_attempts counter exceeds this, the task dead-letters it
# to 'failed' instead of running again.
MAX_ASSESS_ATTEMPTS = 3

# Minimum length (issue #144) for a lead's freeform `description` to count as
# "substantial" enough, on its own, to carry an assessment. Below this, a
# deckless lead also needs usable scraped website content to escape the
# awaiting_deck gate below.
MIN_DESCRIPTION_CHARS = 40


def _mark_failed(lead_id: str, error: str) -> None:
    """Sync DB write to flip a stuck 'processing' lead to 'failed'.
    Called when retries are exhausted so the UI doesn't show a spinner forever."""
    from sqlalchemy import create_engine
    from app.config import settings

    url, connect_args = copper_writer._psycopg2_url_and_connect_args(settings.database_url)
    engine = create_engine(url, connect_args=connect_args)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE leads SET status='failed' WHERE id=:lid AND status IN ('processing','pending')"),
            {"lid": lead_id},
        )
    print(f"[assess_lead] marked lead {lead_id} as failed: {error}")


def _increment_attempts(lead_id: str) -> int:
    """Sync DB write: atomically bump leads.assessment_attempts and return the
    new count. Called at the very start of every task attempt -- including a
    worker-lost redelivery of a task that just OOM'd/hard-killed the worker --
    so the counter survives regardless of whether this same attempt goes on to
    crash the worker again."""
    from sqlalchemy import create_engine
    from app.config import settings

    url, connect_args = copper_writer._psycopg2_url_and_connect_args(settings.database_url)
    engine = create_engine(url, connect_args=connect_args)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "UPDATE leads SET assessment_attempts = assessment_attempts + 1 "
                "WHERE id=:lid RETURNING assessment_attempts"
            ),
            {"lid": lead_id},
        ).first()
    return row[0] if row else 0


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    task_reject_on_worker_lost=True,
    # A hung DeepSeek/Tavily call must fail the task cleanly instead of SIGKILL-ing
    # the whole worker: soft raises SoftTimeLimitExceeded inside the task (caught
    # below) with 60s of headroom to unwind before Celery force-kills it at hard.
    soft_time_limit=240,
    time_limit=300,
)
def assess_lead_task(self, lead_id: str) -> dict:
    attempts = _increment_attempts(lead_id)
    if attempts > MAX_ASSESS_ATTEMPTS:
        error = f"exceeded {MAX_ASSESS_ATTEMPTS} assessment attempts (attempt #{attempts})"
        _mark_failed(lead_id, error)
        return {"lead_id": lead_id, "status": "failed", "error": error}

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run(lead_id))
        finally:
            loop.close()
    except SoftTimeLimitExceeded as exc:
        _mark_failed(lead_id, repr(exc))
        return {"lead_id": lead_id, "status": "failed", "error": "soft time limit exceeded"}
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

        has_deck = bool(lead.pitch_deck_text)

        # No deck yet (issue #144): scrape the company's own public website so
        # its landing page + description can substitute for a pitch deck,
        # instead of parking every no-deck lead unassessed. Best-effort and
        # bounded (see research.scrape_website_content) -- never raises, and
        # is skipped entirely when a deck is already present.
        website_content = (
            research.scrape_website_content(lead.website)
            if not has_deck and lead.website
            else ""
        )
        has_website_content = bool(website_content.strip())
        has_substantial_description = bool(
            lead.description and len(lead.description.strip()) >= MIN_DESCRIPTION_CHARS
        )

        # Park only when there's genuinely no usable context at all: no deck,
        # no usable scraped website content, and a thin/empty description.
        # sync_pitch_decks_task / the per-lead sync endpoint re-queue this
        # task once pitch_deck_text is set, at which point a deck-backed
        # re-assessment refines the score.
        if not has_deck and not has_website_content and not has_substantial_description:
            lead.status = "awaiting_deck"
            lead.assessment_attempts = 0
            await log_event(db, lead.id, EVENT_AWAITING_DECK)
            await db.commit()
            return {"lead_id": lead_id, "status": "awaiting_deck"}

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
        # Broaden the scoring context with the scraped site (issue #144) --
        # research_data is JSON-dumped whole into the assessment prompt's
        # "Research data" section, so this reaches the LLM without touching
        # the prompt template itself.
        if website_content:
            research_data["company_website_content"] = website_content

        # Feedback→pattern loop: pull the team's recent labeled judgments on
        # similar leads and feed them to the assessor as calibration. Best-effort
        # — never let the loop break the assessment.
        try:
            from app.services import feedback_patterns
            _match_text = " ".join(
                filter(None, [lead.description or "", (lead.pitch_deck_text or "")[:3000]])
            )
            team_calibration = await feedback_patterns.retrieve_labeled_exemplars(
                db, _match_text, k=4, exclude_lead_id=lead.id
            )
        except Exception as exc:
            print(f"[assess_lead] exemplar retrieval failed (continuing): {exc!r}")
            team_calibration = []

        # Draft generation should carry the lead owner's own Calendly link +
        # name (issue #84), not a single hardcoded associate's. Best-effort:
        # an owner not (yet) provisioned as a User just falls back to the
        # defaults inside claude_agent.assess_lead.
        owner = None
        if lead.owner_email:
            owner_result = await db.execute(select(User).where(User.email == lead.owner_email))
            owner = owner_result.scalar_one_or_none()

        assessment_result = claude_agent.assess_lead(
            lead_data,
            research_data,
            team_calibration=team_calibration,
            owner_calendly=owner.calendly_url if owner else None,
            owner_name=owner.full_name if owner else None,
        )

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
            precedents_cited=assessment_result.get("precedents_cited"),
            # True when this score was made without a pitch deck (website
            # and/or description only) -- issue #144 -- so partners know it's
            # lower-confidence and can attach a deck to refine it later.
            assessed_without_deck=not has_deck,
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
        lead.assessment_attempts = 0
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
