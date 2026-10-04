from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import select

from app.database import CelerySessionLocal
from app.models.assessment import AssessmentCard
from app.models.bulk_rejection import BulkRejectionBatchItem
from app.models.lead import Lead
from app.models.user import User
from app.services import bulk_rejection, copper_writer, email_sender
from app.services.events import EVENT_DRAFT_APPROVED, log_event
from app.services.override_capture import capture_override
from app.tasks.celery_app import celery


@celery.task(acks_late=True, task_reject_on_worker_lost=True)
def send_bulk_rejection_task(batch_id: str, lead_id: str, actor_email: Optional[str] = None) -> dict:
    """Per-lead send for POST /leads/bulk-send-rejection (issue #205).

    The router dispatches one of these per eligible lead, each with an
    increasing `countdown` (see settings.bulk_rejection_send_interval_seconds)
    so a large batch is throttled rather than firing all at once, and each
    task's failure is isolated from the rest of the batch.

    Reuses `assessments._finalize_sent` -- the exact finalize code
    POST /assessments/{id}/send uses -- so bulk and single-lead sends can
    never drift apart. Idempotent on AssessmentCard.sent_at: a Celery
    redelivery, or re-running the same batch, lands on the eligibility
    re-check below and is recorded `skipped`/`already_sent` rather than
    resending.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run(batch_id, lead_id, actor_email))
        finally:
            loop.close()
    except Exception as exc:
        print(f"[send_bulk_rejection] lead {lead_id} (batch {batch_id}) failed: {exc!r}")
        return {"batch_id": batch_id, "lead_id": lead_id, "status": "failed", "error": repr(exc)}


async def _get_item(db, batch_id: str, lead_id: str) -> Optional[BulkRejectionBatchItem]:
    result = await db.execute(
        select(BulkRejectionBatchItem).where(
            BulkRejectionBatchItem.batch_id == uuid.UUID(batch_id),
            BulkRejectionBatchItem.lead_id == uuid.UUID(lead_id),
        )
    )
    return result.scalar_one_or_none()


async def _resolve(db, item: Optional[BulkRejectionBatchItem], batch_id: str, lead_id: str, status: str, reason: Optional[str]) -> dict:
    if item:
        item.status = status
        item.reason = reason
        await db.commit()
    return {"batch_id": batch_id, "lead_id": lead_id, "status": status, "reason": reason}


async def _run(batch_id: str, lead_id: str, actor_email: Optional[str]) -> dict:
    async with CelerySessionLocal() as db:
        item = await _get_item(db, batch_id, lead_id)
        try:
            return await _send_and_finalize(db, item, batch_id, lead_id, actor_email)
        except Exception as exc:
            print(f"[send_bulk_rejection] lead {lead_id} (batch {batch_id}) failed: {exc!r}")
            return await _resolve(db, item, batch_id, lead_id, "failed", repr(exc))


async def _send_and_finalize(
    db, item: Optional[BulkRejectionBatchItem], batch_id: str, lead_id: str, actor_email: Optional[str]
) -> dict:
    lead_result = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
    lead = lead_result.scalar_one_or_none()

    card = None
    if lead:
        card_result = await db.execute(
            select(AssessmentCard)
            .where(AssessmentCard.lead_id == lead.id)
            .order_by(AssessmentCard.created_at.desc())
            .limit(1)
        )
        card = card_result.scalar_one_or_none()

    if not lead or not card:
        return await _resolve(db, item, batch_id, lead_id, "failed", "lead_or_assessment_missing")

    # Re-check eligibility: the preview -- and even the send endpoint's own
    # re-check -- may be minutes old by the time this throttled task actually
    # runs. This is also what makes a retried/duplicate task idempotent on
    # sent_at: a lead already sent resolves here as `skipped`, never resent.
    reason = bulk_rejection.eligibility_reason(lead, card)
    if reason:
        return await _resolve(db, item, batch_id, lead_id, "skipped", reason)

    recipient = bulk_rejection.recipient_email(lead)

    owner_addr = None
    if lead.owner_email:
        owner_result = await db.execute(select(User).where(User.email == lead.owner_email))
        owner = owner_result.scalar_one_or_none()
        if owner and owner.email:
            owner_addr = owner.email

    try:
        email_sender.send_email(
            recipient,
            card.draft_subject or "",
            card.draft_body,
            reply_to=owner_addr,
            bcc=owner_addr,
        )
    except Exception as exc:
        return await _resolve(db, item, batch_id, lead_id, "failed", f"send_failed: {exc!r}")

    # Commit sent_at (and the batch item row) immediately -- *before* the
    # Copper approve write and _finalize_sent's LLM/Copper work, which can
    # take several seconds. The task is acks_late=True /
    # task_reject_on_worker_lost=True, so a worker lost in that window gets
    # this task redelivered; without committing here first, the redelivery's
    # eligibility re-check would still see sent_at=NULL and email the founder
    # a second time. _finalize_sent re-setting sent_at below is harmless.
    card.sent_at = datetime.now(timezone.utc)
    await db.commit()
    if item:
        item.status = "sent"
        await db.commit()

    # Mark approved (idempotent) + Copper approve tag, mirroring
    # assessments.send_assessment -- bulk and single-lead sends must leave the
    # lead in the same intermediate state before finalize.
    if not card.approved_at:
        card.approved_at = datetime.now(timezone.utc)
        lead.status = "approved"
        await log_event(db, lead.id, EVENT_DRAFT_APPROVED, {"draft_type": card.draft_type})
        await db.commit()
        if lead.copper_id:
            try:
                existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
                copper_writer.mark_approved_in_copper(lead.copper_id, existing_tags)
            except Exception as exc:
                print(f"[send_bulk_rejection] Copper approve write failed: {exc!r}")

    # Shared finalize with the single-lead send path (sent_at, archive, Copper
    # Unqualified write-back, undo snapshot) -- imported lazily to avoid a
    # circular import (assessments.py doesn't import this task module, but
    # app.routers is heavier to import at module load than a worker needs).
    from app.routers.assessments import _finalize_sent

    actor = SimpleNamespace(email=actor_email) if actor_email else None
    await _finalize_sent(db, card, lead, actor)

    # Training signal, same as every other disposition -- but the per-lead
    # rating gate doesn't apply here (bulk-archive precedent): selecting and
    # confirming the batch *is* the human judgement.
    effective_bucket = card.user_override or card.bucket
    await capture_override(
        db, lead=lead, card=card, human_bucket=effective_bucket,
        trigger="bulk_send_rejection", acted_by_email=actor_email,
    )

    return await _resolve(db, item, batch_id, lead_id, "sent", None)
