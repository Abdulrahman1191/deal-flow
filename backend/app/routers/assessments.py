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
from app.services import claude_agent, copper_writer, email_sender
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


async def _get_card_and_lead(lead_id: str, db: AsyncSession, user: User) -> tuple[AssessmentCard, Lead]:
    """Fetch the latest assessment card for lead_id, scoped to the caller's
    own lead (join + `Lead.owner_email == user.email`), mirroring the pattern
    already used throughout leads.py. Returns 404 — not 403 — when the lead
    belongs to someone else, so a cross-user probe can't distinguish "doesn't
    exist" from "not yours" (SECURITY_AUDIT.md F2: this router previously had
    no ownership check at all)."""
    result = await db.execute(
        select(AssessmentCard, Lead)
        .join(Lead, AssessmentCard.lead_id == Lead.id)
        .where(AssessmentCard.lead_id == lead_id, Lead.owner_email == user.email)
        .order_by(AssessmentCard.created_at.desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return row


def _require_rating(card: AssessmentCard) -> None:
    """Enforced learning: a human 👍/👎 on the AI recommendation is MANDATORY
    before a lead can be approved or sent (meeting request or rejection). Raises
    428 if the card is unrated. NOTE: a bucket override alone does NOT satisfy
    this — the reviewer must explicitly rate up or down."""
    if not card.user_rating:
        raise HTTPException(
            status_code=428,
            detail="Rate the recommendation (👍 or 👎) before approving or sending this lead.",
        )


@router.get("/send-queue")
async def get_send_queue(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Returns the caller's own approved but unsent email drafts for Cowork to
    action. Scoped to `Lead.owner_email == user.email` like every other
    endpoint in this router (SECURITY_AUDIT.md F2) — the caller's Cowork
    integration only ever mark-sents leads it owns, so an unscoped queue would
    both leak cross-user draft content and list items `mark-sent` 404s on."""
    result = await db.execute(
        select(AssessmentCard, Lead)
        .join(Lead, AssessmentCard.lead_id == Lead.id)
        .where(
            and_(
                AssessmentCard.approved_at.is_not(None),
                AssessmentCard.sent_at.is_(None),
                AssessmentCard.draft_type.is_not(None),
                Lead.owner_email == user.email,
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
    user: User = Depends(get_current_user),
):
    card, _lead = await _get_card_and_lead(lead_id, db, user)
    return card


@router.post("/{lead_id}/approve")
async def approve_assessment(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Marks the draft as approved and queues it for sending."""
    card, lead = await _get_card_and_lead(lead_id, db, user)
    _require_rating(card)

    if card.approved_at:
        return {"status": "already_approved"}

    card.approved_at = datetime.now(timezone.utc)
    lead.status = "approved"
    await log_event(db, lead.id, EVENT_DRAFT_APPROVED, {"draft_type": card.draft_type})

    await db.commit()

    # F9 — sync approval to Copper via outbox (best-effort; don't fail the local commit)
    if lead.copper_id:
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            copper_writer.mark_approved_in_copper(lead.copper_id, existing_tags)
        except Exception as exc:
            print(f"[approve_assessment] Copper write failed: {exc!r}")

    # Capture as training data — Approve is an implicit confirmation of the
    # effective bucket (user_override or bucket). human_bucket == that value.
    effective = card.user_override or card.bucket
    await capture_override(
        db, lead=lead, card=card, human_bucket=effective, trigger="approve",
        acted_by_email=user.email,
    )

    return {"status": "approved", "draft_type": card.draft_type}


async def _finalize_sent(db: AsyncSession, card: AssessmentCard, lead: Optional[Lead]) -> dict:
    """Terminal transition once an email has actually been sent:
      - rejection draft  → app archived + Copper Lead → Unqualified
      - meeting_request  → app archived + Copper Lead converted to Opportunity
    Shared by /mark-sent (external sender) and /send (in-app sender)."""
    card.sent_at = datetime.now(timezone.utc)
    if not lead:
        await db.commit()
        return {"status": "sent", "sent_at": card.sent_at.isoformat()}

    await log_event(db, lead.id, EVENT_EMAIL_SENT, {"draft_type": card.draft_type})

    converted_payload: Optional[dict] = None
    existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
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
        print(f"[finalize_sent] Copper write failed (local commit succeeded): {exc!r}")

    lead.status = "archived"
    await log_event(db, lead.id, EVENT_ARCHIVED, {"reason": card.draft_type or "sent"})
    await db.commit()
    return {
        "status": "sent",
        "sent_at": card.sent_at.isoformat(),
        "outcome": card.draft_type,
        "converted": bool(converted_payload),
    }


@router.post("/{lead_id}/mark-sent")
async def mark_sent(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Called by an EXTERNAL sender (e.g. Cowork) after it sends the email.
    Just records 'sent' + runs the terminal Copper transition — does NOT send."""
    card, lead = await _get_card_and_lead(lead_id, db, user)
    _require_rating(card)
    return await _finalize_sent(db, card, lead)


@router.post("/{lead_id}/send")
async def send_assessment(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Actually SEND the drafted email (via SMTP — SES/SendGrid), then finalize.

    Refuses to proceed unless email is configured and a recipient + draft exist —
    so a lead is never marked sent / converted in Copper without a real email
    going out. On a send failure we abort (no state change) so it can be retried.
    """
    card, lead = await _get_card_and_lead(lead_id, db, user)
    _require_rating(card)

    if not email_sender.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Email isn't configured yet. Set SMTP_HOST + MAIL_FROM (SES or SendGrid) in the app env.",
        )
    recipient = (lead.raw_copper_data or {}).get("recipient_email") if lead.raw_copper_data else None
    if not recipient:
        raise HTTPException(status_code=400, detail="This lead has no recipient email address.")
    if not card.draft_body:
        raise HTTPException(status_code=400, detail="There's no draft to send for this lead.")

    # Send first — if it fails, nothing changes and the user can retry.
    try:
        email_sender.send_email(recipient, card.draft_subject or "", card.draft_body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Email send failed: {exc}")

    # Mark approved (idempotent) + Copper approve tag + training capture.
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
                print(f"[send] Copper approve write failed: {exc!r}")
        await capture_override(
            db, lead=lead, card=card, human_bucket=(card.user_override or card.bucket), trigger="approve",
            acted_by_email=user.email,
        )

    result = await _finalize_sent(db, card, lead)
    result["recipient"] = recipient
    return result


@router.patch("/{lead_id}/draft", response_model=AssessmentOut)
async def update_draft(
    lead_id: str,
    body: DraftUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    card, lead = await _get_card_and_lead(lead_id, db, user)
    changed = body.model_dump(exclude_none=True)
    for field, value in changed.items():
        setattr(card, field, value)
    await db.commit()
    await db.refresh(card)

    # F8 — sync edited draft text to Copper custom fields (best-effort)
    if changed and lead.copper_id:
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
    user: User = Depends(get_current_user),
):
    if body.bucket not in ("YES", "MAYBE", "REJECT"):
        raise HTTPException(status_code=400, detail="bucket must be YES, MAYBE, or REJECT")
    card, lead = await _get_card_and_lead(lead_id, db, user)
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
    await log_event(
        db,
        lead.id,
        EVENT_BUCKET_OVERRIDDEN,
        {"from": prior_bucket, "to": body.bucket},
    )
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
    if lead.copper_id:
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            copper_writer.set_bucket_tag(lead.copper_id, body.bucket, existing_tags)
        except Exception as exc:
            print(f"[override_bucket] Copper write failed (local commit succeeded): {exc!r}")

    # Capture for training. We record the AI's view at the moment of override —
    # for re-overrides this is the previous human bucket, which the downstream
    # eval scripts filter out using `trigger='override' AND was_first_override`
    # (see LLM_TUNING_PLAN.md). We pass `ai_bucket` explicitly rather than
    # mutating the live card: capture_override commits internally, so mutating
    # card.bucket here would persist the snapshot value over the real bucket.
    await capture_override(
        db, lead=lead, card=card, human_bucket=body.bucket,
        trigger="override" if was_first_override else "re-override",
        reason_tags=body.reason_tags,
        reason=body.reason,
        ai_bucket=ai_bucket_snapshot,
        acted_by_email=user.email,
    )

    return card


@router.post("/{lead_id}/rate", response_model=AssessmentOut)
async def rate_assessment(
    lead_id: str,
    body: AssessmentRating,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
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

    card, lead = await _get_card_and_lead(lead_id, db, user)
    effective_bucket = card.user_override or card.bucket

    card.user_rating = body.rating
    card.user_rating_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(card)

    await capture_override(
        db,
        lead=lead,
        card=card,
        human_bucket=effective_bucket,
        trigger="confirm" if body.rating == "up" else "rate_down",
        reason_tags=body.reason_tags,
        reason=body.reason,
        acted_by_email=user.email,
    )

    return card


@router.post("/{lead_id}/regenerate-draft", response_model=AssessmentOut)
async def regenerate_draft(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Force the LLM to write a fresh draft email matching the current effective bucket.
    Used when a draft is missing (silent regen failure) or the user wants a rewrite."""
    card, lead = await _get_card_and_lead(lead_id, db, user)

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
    user: User = Depends(get_current_user),
):
    lead_result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email)
    )
    lead = lead_result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = "pending"
    await db.commit()
    assess_lead_task.delay(lead_id)
    return {"status": "queued"}
