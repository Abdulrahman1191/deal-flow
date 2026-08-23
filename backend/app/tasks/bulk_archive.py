from __future__ import annotations
import asyncio
import uuid

from sqlalchemy import select

from app.database import CelerySessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services import claude_agent, copper_writer
from app.tasks.celery_app import celery


@celery.task(bind=True, max_retries=3, default_retry_delay=30)
def bulk_archive_copper_writeback_task(self, lead_id: str) -> dict:
    """Per-lead follow-up to POST /leads/bulk-archive: generates the
    Unqualification Reasons + Details via the AI (driven by the lead's latest
    assessment, exactly like the single-lead archive-no-reply path) and pushes
    the Copper Unqualified write-back. Runs out-of-request so bulk-archiving N
    leads never blocks the HTTP response on N synchronous LLM calls.

    Best-effort end to end: no assessment or a failed AI call still archives
    the lead in Copper, just with the reason field left blank -- this task
    must never be the reason a lead fails to leave 'My Open Leads'.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run(lead_id))
        finally:
            loop.close()
    except Exception as exc:
        print(f"[bulk_archive_copper_writeback] lead {lead_id} failed: {exc!r}")
        return {"lead_id": lead_id, "status": "failed", "error": repr(exc)}


async def _run(lead_id: str) -> dict:
    async with CelerySessionLocal() as db:
        result = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
        lead = result.scalar_one_or_none()
        if not lead or not lead.copper_id or lead.copper_opportunity_id:
            return {"lead_id": lead_id, "status": "skipped"}

        card_result = await db.execute(
            select(AssessmentCard).where(AssessmentCard.lead_id == lead.id)
            .order_by(AssessmentCard.created_at.desc()).limit(1)
        )
        card = card_result.scalar_one_or_none()

        reason_option_ids, detail_text = None, None
        if card:
            try:
                unqual = claude_agent.generate_unqualification_reason(
                    company_name=lead.company_name,
                    bucket=card.user_override or card.bucket,
                    summary=card.summary,
                    red_flags=card.red_flags,
                )
                reason_option_ids = unqual.get("reason_option_ids")
                detail_text = unqual.get("detail_text")
            except Exception as exc:
                print(
                    f"[bulk_archive_copper_writeback] AI reason generation failed for "
                    f"lead {lead.id} (archiving anyway): {exc!r}"
                )

        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            copper_writer.archive_in_copper(
                lead.copper_id, existing_tags,
                reason_option_ids=reason_option_ids, detail_text=detail_text,
            )
        except Exception as exc:
            print(
                f"[bulk_archive_copper_writeback] Copper write failed for lead "
                f"{lead.id}: {exc!r}"
            )
            return {"lead_id": lead_id, "status": "copper_write_failed", "error": repr(exc)}

        return {"lead_id": lead_id, "status": "done"}
