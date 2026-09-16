from __future__ import annotations
"""
Undo service (issue #153 established the archive-only baseline; issue #159
extends it to bucket-override, approve, and bulk-archive).

Every undoable action writes one `lead_action_log` row via a `record_*`
function here, right after the caller has decided what changed (so it can
snapshot the state about to be overwritten). `POST /leads/{lead_id}/undo`
(and the bulk-archive batch-undo endpoint) then call `undo_action` to
restore that snapshot and reverse the Copper write-back.

Action types don't all restore the same thing:
  - archive_no_reply / archive_after_send / bulk_archive restore lead.status
    + Copper tags/status_id via reverse_archive_in_copper.
  - bucket_override restores the assessment card's bucket/user_override/
    draft fields + Copper tags via reverse_bucket_tag (lead.status is
    untouched by an override, so there's nothing to restore there).
  - approve restores card.approved_at + lead.status + Copper tags/custom
    field via reverse_approve_in_copper.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.models.lead_action_log import LeadActionLog
from app.models.override import AssessmentOverride
from app.services import copper_writer
from app.services.events import EVENT_ACTION_UNDONE, log_event

ACTION_ARCHIVE_NO_REPLY = "archive_no_reply"
ACTION_ARCHIVE_AFTER_SEND = "archive_after_send"
ACTION_BULK_ARCHIVE = "bulk_archive"
ACTION_BUCKET_OVERRIDE = "bucket_override"
ACTION_APPROVE = "approve"

# Archive-shaped actions all restore lead.status + Copper tags/status_id the
# same way, whether they came from the single-lead path, the sent-rejection
# path, or one lead inside a bulk-archive batch.
ARCHIVE_ACTIONS = {ACTION_ARCHIVE_NO_REPLY, ACTION_ARCHIVE_AFTER_SEND, ACTION_BULK_ARCHIVE}

# Action types POST /leads/{lead_id}/undo (and the bulk-archive batch-undo
# endpoint) will reverse. Anything else logged to lead_action_log in the
# future is invisible to /undo until it's explicitly added here.
UNDOABLE_ACTIONS = ARCHIVE_ACTIONS | {ACTION_BUCKET_OVERRIDE, ACTION_APPROVE}


def is_within_undo_window(action: LeadActionLog) -> bool:
    """Quick-undo (POST /undo and the batch-undo endpoint) refuses to reverse
    an action older than settings.undo_window_hours (issue #159) -- see the
    config comment for why. Doesn't apply to the separate Archive-page
    restore flow, which never calls into this service."""
    if action.created_at is None:
        return True
    age = datetime.now(timezone.utc) - action.created_at
    return age <= timedelta(hours=settings.undo_window_hours)


async def record_archive_action(
    db: AsyncSession,
    *,
    lead: Lead,
    action_type: str,
    prior_status: str,
    prior_tags: Optional[list],
    actor_email: Optional[str],
    email_sent: bool = False,
    copper_outbox_id: Optional[str] = None,
    batch_id: Optional[uuid.UUID] = None,
) -> LeadActionLog:
    """Snapshot the state an archive action -- single, sent-rejection, or one
    lead within a bulk-archive batch (`batch_id` set) -- is about to
    overwrite. Caller is responsible for the commit (mirrors
    override_capture's contract)."""
    row = LeadActionLog(
        lead_id=lead.id,
        action_type=action_type,
        actor_email=actor_email,
        prior_state={
            "status": prior_status,
            "copper_id": lead.copper_id,
            "copper_tags": prior_tags or [],
        },
        email_sent=email_sent,
        copper_outbox_id=copper_outbox_id,
        batch_id=batch_id,
    )
    db.add(row)
    return row


async def record_bucket_override_action(
    db: AsyncSession,
    *,
    lead: Lead,
    card: AssessmentCard,
    prior_bucket: Optional[str],
    prior_user_override: Optional[str],
    prior_draft_type: Optional[str],
    prior_draft_subject: Optional[str],
    prior_draft_body: Optional[str],
    prior_draft_bucket: Optional[str],
    prior_tags: Optional[list],
    resulting_bucket: str,
    actor_email: Optional[str],
    copper_outbox_id: Optional[str] = None,
) -> LeadActionLog:
    """Snapshot what POST /assessments/{lead_id}/override is about to
    overwrite. A bucket override never touches lead.status, so unlike the
    archive actions there's no lead status to restore -- just the card's
    bucket/user_override/draft fields. `resulting_bucket` (the value this
    override sets) is stashed in prior_state too, so the undo endpoint can
    detect drift -- e.g. a later re-override -- without a second query."""
    row = LeadActionLog(
        lead_id=lead.id,
        card_id=card.id,
        action_type=ACTION_BUCKET_OVERRIDE,
        actor_email=actor_email,
        prior_state={
            "bucket": prior_bucket,
            "user_override": prior_user_override,
            "draft_type": prior_draft_type,
            "draft_subject": prior_draft_subject,
            "draft_body": prior_draft_body,
            "draft_bucket": prior_draft_bucket,
            "copper_tags": prior_tags or [],
            "resulting_bucket": resulting_bucket,
        },
        copper_outbox_id=copper_outbox_id,
    )
    db.add(row)
    return row


async def record_approve_action(
    db: AsyncSession,
    *,
    lead: Lead,
    card: AssessmentCard,
    prior_status: str,
    prior_tags: Optional[list],
    actor_email: Optional[str],
    copper_outbox_id: Optional[str] = None,
) -> LeadActionLog:
    """Snapshot what POST /assessments/{lead_id}/approve is about to
    overwrite: card.approved_at was None and lead.status was prior_status."""
    row = LeadActionLog(
        lead_id=lead.id,
        card_id=card.id,
        action_type=ACTION_APPROVE,
        actor_email=actor_email,
        prior_state={
            "status": prior_status,
            "copper_tags": prior_tags or [],
        },
        copper_outbox_id=copper_outbox_id,
    )
    db.add(row)
    return row


def _apply_archive_undo(lead: Lead, action: LeadActionLog) -> dict:
    prior_state = action.prior_state or {}
    prior_status = prior_state.get("status") or "pending"
    prior_tags = prior_state.get("copper_tags")

    lead.status = prior_status

    copper_enqueued = False
    if lead.copper_id:
        try:
            new_outbox_id = copper_writer.reverse_archive_in_copper(
                lead.copper_id, prior_tags, pending_outbox_id=action.copper_outbox_id,
            )
            copper_enqueued = bool(new_outbox_id)
        except Exception as exc:
            print(f"[undo] Copper reversal failed (local commit succeeded): {exc!r}")

    response = {
        "restored_status": lead.status,
        "copper_enqueued": copper_enqueued,
        "email_sent": action.email_sent,
    }
    if action.email_sent:
        response["note"] = (
            "The original email cannot be unsent. App and Copper state were "
            "restored, but the recipient already received it."
        )
    return response


def _apply_bucket_override_undo(lead: Lead, card: AssessmentCard, action: LeadActionLog) -> dict:
    prior_state = action.prior_state or {}

    card.bucket = prior_state.get("bucket")
    card.user_override = prior_state.get("user_override")
    card.draft_type = prior_state.get("draft_type")
    card.draft_subject = prior_state.get("draft_subject")
    card.draft_body = prior_state.get("draft_body")
    card.draft_bucket = prior_state.get("draft_bucket")

    copper_enqueued = False
    if lead.copper_id:
        try:
            new_outbox_id = copper_writer.reverse_bucket_tag(
                lead.copper_id, prior_state.get("copper_tags"), pending_outbox_id=action.copper_outbox_id,
            )
            copper_enqueued = bool(new_outbox_id)
        except Exception as exc:
            print(f"[undo] Copper reversal failed (local commit succeeded): {exc!r}")

    return {
        "restored_bucket": card.bucket,
        "copper_enqueued": copper_enqueued,
        "email_sent": False,
    }


def _apply_approve_undo(lead: Lead, card: AssessmentCard, action: LeadActionLog) -> dict:
    prior_state = action.prior_state or {}
    prior_status = prior_state.get("status") or "pending"

    card.approved_at = None
    lead.status = prior_status

    copper_enqueued = False
    if lead.copper_id:
        try:
            new_outbox_id = copper_writer.reverse_approve_in_copper(
                lead.copper_id, prior_state.get("copper_tags"), pending_outbox_id=action.copper_outbox_id,
            )
            copper_enqueued = bool(new_outbox_id)
        except Exception as exc:
            print(f"[undo] Copper reversal failed (local commit succeeded): {exc!r}")

    return {
        "restored_status": lead.status,
        "copper_enqueued": copper_enqueued,
        "email_sent": False,
    }


async def undo_action(
    db: AsyncSession, *, lead: Lead, action: LeadActionLog, card: Optional[AssessmentCard] = None,
) -> dict:
    """Reverses `action` against `lead` (and `card`, for the action types
    that changed one): restores the snapshot, enqueues a Copper write-back
    reversing it, marks the action + its training row consumed, and logs an
    `action_undone` event. Caller has already validated the action is
    undoable, unconsumed, in-window, and that current state still matches
    what the action produced (see the router)."""
    if action.action_type in ARCHIVE_ACTIONS:
        response = _apply_archive_undo(lead, action)
    elif action.action_type == ACTION_BUCKET_OVERRIDE:
        assert card is not None
        response = _apply_bucket_override_undo(lead, card, action)
    elif action.action_type == ACTION_APPROVE:
        assert card is not None
        response = _apply_approve_undo(lead, card, action)
    else:
        raise ValueError(f"undo_action: unsupported action_type {action.action_type!r}")

    action.undone_at = datetime.now(timezone.utc)

    await log_event(db, lead.id, EVENT_ACTION_UNDONE, {
        "reversed_action": action.action_type,
        "email_sent": action.email_sent,
    })

    # Don't poison the learning loop: the most recent not-yet-reverted
    # training row for this lead is the one this action produced. Mark it
    # reverted so it drops out of feedback_patterns.retrieve_labeled_exemplars
    # and the calibration/overrides stats.
    ov_result = await db.execute(
        select(AssessmentOverride)
        .where(AssessmentOverride.lead_id == lead.id)
        .where(AssessmentOverride.reverted_at.is_(None))
        .order_by(desc(AssessmentOverride.created_at))
        .limit(1)
    )
    override_row = ov_result.scalar_one_or_none()
    if override_row:
        override_row.reverted_at = datetime.now(timezone.utc)

    await db.commit()

    response["status"] = "undone"
    response["action_type"] = action.action_type
    return response
