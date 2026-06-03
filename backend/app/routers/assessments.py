from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.models.user import User
from app.schemas.assessment import AssessmentOut, AssessmentRating, BucketOverride, DraftUpdate
from app.services import claude_agent, copper_writer
from app.services.auth import get_current_user
from app.services.override_capture import capture_override
from app.services.events import (
    EVENT_ARCHIVED,
    EVENT_BUCKET_OVERRIDDEN,
    EVENT_CONVERTED,
    EVENT_DRAFT_APPROVED,
    EVENT_EMAIL_SENT,
    log_event,
)
from app.tasks.assess_lead import assess_lead_task

router = APIRouter(prefix="/assessments", tags=["assessments"])


async def _get_card(lead_id: str, db: AsyncSession) -> AssessmentCard:
    result = await db.execute(
        select(AssessmentCard)
        .where(AssessmentCard.lead_id == lead_id)
        .order_by(AssessmentCard.created_at.desc())
        .limit(1)
    )
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return card


@router.get("/send-queue")
async def get_send_queue(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Returns all approved but unsent email drafts for Cowork to action."""
    result = await db.execute(
        select(AssessmentCard, Lead)
        .join(Lead, AssessmentCard.lead_id == Lead.id)
        .where(
            and_(
                AssessmentCard.approved_at.is_not(None),
                AssessmentCard.sent_at.is_(None),
                AssessmentCard.draft_type.is_not(None),
            )
        )
        .order_by(AssessmentCard.approved_at.asc())
    )
    rows = result.all()

    return [
        {
            "lead_id": str(card.lead_id),
            "assessment_id": str(card.id),
            "company_name": lead.company_name,
            "draft_type": card.draft_type,
            "recipient_email": (lead.raw_copper_data or {}).get("recipient_email", ""),
            "draft_subject": card.draft_subject,
            "draft_body": card.draft_body,
            "approved_at": card.approved_at.isoformat() if card.approved_at else None,
        }
        for card, lead in rows
    ]


@router.get("/{lead_id}", response_model=AssessmentOut)
async def get_assessment(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await _get_card(lead_id, db)


@router.post("/{lead_id}/approve")
async def approve_assessment(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Marks the draft as approved and queues it for sending."""
    card = await _get_card(lead_id, db)

    if card.approved_at:
        return {"status": "already_approved"}

    card.approved_at = datetime.now(timezone.utc)

    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if lead:
        lead.status = "approved"
        await log_event(db, lead.id, EVENT_DRAFT_APPROVED, {"draft_type": card.draft_type})

    await db.commit()

    # F9 — sync approval to Copper via outbox (best-effort; don't fail the local commit)
    if lead and lead.copper_id:
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            copper_writer.mark_approved_in_copper(lead.copper_id, existing_tags)
        except Exception as exc:
            print(f"[approve_assessment] Copper write failed: {exc!r}")

    # Capture as training data — Approve is an implicit confirmation of the
    # effective bucket (user_override or bucket). human_bucket == that value.
    if lead:
        effective = card.user_override or card.bucket
        await capture_override(
            db, lead=lead, card=card, human_bucket=effective, trigger="approve",
        )

    return {"status": "approved", "draft_type": card.draft_type}


@router.post("/{lead_id}/mark-sent")
async def mark_sent(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Called by Cowork after it sends the email via Gmail. This is the trigger that
    moves the lead to its terminal state:
      - rejection draft  → app archived + Copper Lead → Unqualified
      - meeting_request  → app archived + Copper Lead converted to Opportunity
    """
    card = await _get_card(lead_id, db)
    card.sent_at = datetime.now(timezone.utc)

    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if not lead:
        await db.commit()
        return {"status": "sent", "sent_at": card.sent_at.isoformat()}

    await log_event(db, lead.id, EVENT_EMAIL_SENT, {"draft_type": card.draft_type})

    # Compute outcome based on draft_type
    converted_payload: Optional[dict] = None
    existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None

    # Use the effective bucket (override wins over original) to guard conversion.
    effective_bucket = card.user_override or card.bucket

    try:
        if card.draft_type == "meeting_request" and effective_bucket == "YES" and lead.copper_id and not lead.copper_opportunity_id:
            founder_name = (lead.founder_names or [None])[0]
            converted_payload = copper_writer.convert_lead_to_opportunity(
                lead.copper_id, lead.company_name, founder_name
            )
            if converted_payload:
                lead.copper_person_id = converted_payload.get("person_id")
                lead.copper_company_id = converted_payload.get("company_id")
                lead.copper_opportunity_id = converted_payload.get("opportunity_id")
                lead.copper_id = None
                await log_event(db, lead.id, EVENT_CONVERTED, converted_payload)
        elif card.draft_type == "rejection" and lead.copper_id:
            copper_writer.archive_in_copper(lead.copper_id, existing_tags)
        elif lead.copper_id:
            copper_writer.mark_sent_in_copper(lead.copper_id, existing_tags)
    except Exception as exc:
        print(f"[mark_sent] Copper write failed (local commit succeeded): {exc!r}")

    # Local terminal state
    lead.status = "archived"
    await log_event(db, lead.id, EVENT_ARCHIVED, {"reason": card.draft_type or "sent"})

    await db.commit()
    return {
        "status": "sent",
        "sent_at": card.sent_at.isoformat(),
        "outcome": card.draft_type,
        "converted": bool(converted_payload),
    }


@router.patch("/{lead_id}/draft", response_model=AssessmentOut)
async def update_draft(
    lead_id: str,
    body: DraftUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    card = await _get_card(lead_id, db)
    changed = body.model_dump(exclude_none=True)
    for field, value in changed.items():
        setattr(card, field, value)
    await db.commit()
    await db.refresh(card)

    # F8 — sync edited draft text to Copper custom fields (best-effort)
    if changed:
        lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead and lead.copper_id:
            try:
                copper_writer.push_draft_edit(
                    lead.copper_id,
                    draft_subject=changed.get("draft_subject"),
                    draft_body=changed.get("draft_body"),
                )
            except Exception as exc:
                print(f"[update_draft] Copper write failed: {exc!r}")
    return card


@router.post("/{lead_id}/override", response_model=AssessmentOut)
async def override_bucket(
    lead_id: str,
    body: BucketOverride,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if body.bucket not in ("YES", "MAYBE", "REJECT"):
        raise HTTPException(status_code=400, detail="bucket must be YES, MAYBE, or REJECT")
    card = await _get_card(lead_id, db)
    if card.bucket == body.bucket and not card.user_override:
        return card  # no-op

    prior_bucket = card.user_override or card.bucket
    # `ai_bucket_at_override` = the AI's most recent calculation. If this is the
    # first override, that's card.bucket (untouched AI output). If it's a
    # re-override, prior_bucket is the previous human override, so we use card.bucket
    # as recorded which was last set by AI when reassessed; for training-data
    # purity, downstream queries should filter to rows where prior user_override
    # was NULL.
    ai_bucket_snapshot = card.bucket if card.user_override is None else (card.user_override or card.bucket)
    was_first_override = card.user_override is None

    card.user_override = body.bucket
    card.user_override_at = datetime.now(timezone.utc)
    card.bucket = body.bucket

    # Regenerate the draft email to match the new bucket.
    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if lead:
        await log_event(
            db,
            lead.id,
            EVENT_BUCKET_OVERRIDDEN,
            {"from": prior_bucket, "to": body.bucket},
        )
    if lead:
        try:
            new_draft = claude_agent.regenerate_draft(
                {"company_name": lead.company_name, "founder_names": lead.founder_names},
                body.bucket,
                card.summary or "",
            )
            card.draft_type = new_draft.get("draft_type")
            card.draft_subject = new_draft.get("draft_subject")
            card.draft_body = new_draft.get("draft_body")
        except Exception as exc:
            # Don't fail the override if the LLM call hiccups — user can hit "Re-assess".
            print(f"[override_bucket] draft regen failed for lead {lead_id}: {exc!r}")

    await db.commit()
    await db.refresh(card)

    # Mirror to Copper (best-effort): tag swap only.
    if lead and lead.copper_id:
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            copper_writer.set_bucket_tag(lead.copper_id, body.bucket, existing_tags)
        except Exception as exc:
            print(f"[override_bucket] Copper write failed (local commit succeeded): {exc!r}")

    # Capture for training. Note we capture the AI's view at the moment of
    # override — for re-overrides this is the previous human bucket, which the
    # downstream eval scripts filter out using `trigger='override' AND
    # was_first_override` (see LLM_TUNING_PLAN.md).
    if lead:
        # Reset ai_bucket on the in-memory card so the helper sees the original AI call
        original_bucket = card.bucket
        card.bucket = ai_bucket_snapshot
        try:
            await capture_override(
                db, lead=lead, card=card, human_bucket=body.bucket,
                trigger="override" if was_first_override else "re-override",
                reason_tags=body.reason_tags,
                reason=body.reason,
            )
        finally:
            # restore for the response
            card.bucket = original_bucket

    return card


@router.post("/{lead_id}/rate", response_model=AssessmentOut)
async def rate_assessment(
    lead_id: str,
    body: AssessmentRating,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Thumbs up/down on the AI recommendation — enforced-learning signal.

    Unlike /override this does NOT change the bucket or regenerate the draft.
    "up" registers agreement with the current effective bucket; "down" registers
    disagreement. Both snapshot a training row into assessment_overrides so the
    model can later be tuned against the human's judgement — including the cases
    where the AI was *right*, which an override-only flow never captures.
    """
    if body.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")

    card = await _get_card(lead_id, db)
    effective_bucket = card.user_override or card.bucket

    card.user_rating = body.rating
    card.user_rating_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(card)

    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if lead:
        await capture_override(
            db,
            lead=lead,
            card=card,
            human_bucket=effective_bucket,
            trigger="confirm" if body.rating == "up" else "rate_down",
            reason_tags=body.reason_tags,
            reason=body.reason,
        )

    return card


@router.post("/{lead_id}/regenerate-draft", response_model=AssessmentOut)
async def regenerate_draft(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Force the LLM to write a fresh draft email matching the current effective bucket.
    Used when a draft is missing (silent regen failure) or the user wants a rewrite."""
    card = await _get_card(lead_id, db)
    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    effective_bucket = card.user_override or card.bucket
    if effective_bucket not in ("YES", "REJECT"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot regenerate draft for bucket={effective_bucket}",
        )

    try:
        new_draft = claude_agent.regenerate_draft(
            {"company_name": lead.company_name, "founder_names": lead.founder_names},
            effective_bucket,
            card.summary or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc!r}")

    card.draft_type = new_draft.get("draft_type")
    card.draft_subject = new_draft.get("draft_subject")
    card.draft_body = new_draft.get("draft_body")
    await db.commit()
    await db.refresh(card)
    return card


@router.post("/{lead_id}/reassess", status_code=202)
async def reassess(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    lead_result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = "pending"
    await db.commit()
    assess_lead_task.delay(lead_id)
    return {"status": "queued"}
