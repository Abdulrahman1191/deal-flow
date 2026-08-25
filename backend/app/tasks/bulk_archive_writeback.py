from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import CelerySessionLocal
from app.models.lead import Lead
from app.models.assessment import AssessmentCard
from app.services import claude_agent, copper_writer
from app.tasks.celery_app import celery


@celery.task(acks_late=True, task_reject_on_worker_lost=True)
def bulk_archive_writeback_task(lead_id: str) -> dict:
    """Per-lead Copper write-back for POST /leads/bulk-archive (issue #141).

    The router flips status=archived + logs the LeadEvent synchronously (fast,
    no network/LLM calls) so the bulk request returns immediately, then hands
    this task off per lead. This is where the slow part happens: an LLM call
    to auto-generate the Unqualification Reasons/Details, then the Copper
    write-back carrying them. Best-effort throughout -- a lead with no
    assessment card, or a failed AI/Copper call, still ends up Unqualified in
    Copper; it just may be missing the reason fields.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run(lead_id))
        finally:
            loop.close()
    except Exception as exc:
        print(f"[bulk_archive_writeback] lead {lead_id} failed: {exc!r}")
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
                    f"[bulk_archive_writeback] Unqualification-reason AI call failed for "
                    f"lead {lead.id} (writing back anyway): {exc!r}"
                )

        existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
        wrote_unqualified = copper_writer.archive_in_copper(
            lead.copper_id, existing_tags,
            reason_option_ids=reason_option_ids, detail_text=detail_text,
        )
        # issue #157 — remember Copper now reads Unqualified so a later
        # REJECT->YES/MAYBE override knows to correct it back.
        if wrote_unqualified:
            lead.copper_unqualified_at = datetime.now(timezone.utc)
            await db.commit()
        return {"lead_id": lead_id, "status": "written_back"}
