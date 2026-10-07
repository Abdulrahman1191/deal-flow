from __future__ import annotations
"""
Undo service (issue #153, narrowed to archive-only for this PR; issue #225
adds the one bucket-override action below).

Two halves:
  - `record_archive_action` / `record_bucket_override_action` -- called from
    the action call sites right after they've decided what changed, to
    snapshot the state about to be overwritten.
  - `undo_action` -- called from POST /leads/{lead_id}/undo to restore that
    snapshot and reverse the Copper write-back.

Manual override_bucket undo, approve undo, bulk-archive undo, the frontend
toast, and the calibration-stats exclusion are deferred follow-ups -- see
the issue.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.models.lead_action_log import LeadActionLog
from app.models.override import AssessmentOverride
from app.services import copper_writer
from app.services.events import EVENT_ACTION_UNDONE, log_event

ACTION_ARCHIVE_NO_REPLY = "archive_no_reply"
ACTION_ARCHIVE_AFTER_SEND = "archive_after_send"
ACTION_DELETE = "delete"
ACTION_BULK_ARCHIVE = "bulk_archive"
# A bucket override that moved the lead's bucket/draft (issue #225, for now
# only the thumbs-down-on-YES auto-reject path -- manual override_bucket
# undo remains a deferred follow-up). Restores the assessment card, not
# lead.status -- see _undo_bucket_override below.
ACTION_BUCKET_OVERRIDE = "bucket_override"

# Action types POST /leads/{lead_id}/undo will reverse. Anything else logged
# to lead_action_log in the future (approve, ...) is invisible to /undo until
# it's explicitly added here.
UNDOABLE_ACTIONS = {ACTION_ARCHIVE_NO_REPLY, ACTION_ARCHIVE_AFTER_SEND, ACTION_BUCKET_OVERRIDE}

# All action types that represent a lead being written back to Copper as
# Unqualified. Superset of UNDOABLE_ACTIONS -- delete_lead and bulk-archive
# aren't wired into /undo yet, but they call copper_writer.archive_in_copper
# exactly like archive_no_reply/archive_after_send do. override_bucket
# (issue #157) queries this set instead of a dedicated Lead column to decide
# whether a REJECT->YES/MAYBE override needs to correct a stale Unqualified
# disposition: an unresolved (undone_at IS NULL) row here already means "we
# wrote Unqualified and haven't reversed it", and reusing it means archives
# that never touch Copper (dedup, the Copper-delete webhook mirror) can't be
# mistaken for one, since they never log to this table.
UNQUALIFIED_WRITE_ACTIONS = {
    ACTION_ARCHIVE_NO_REPLY, ACTION_ARCHIVE_AFTER_SEND, ACTION_DELETE, ACTION_BULK_ARCHIVE,
}


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
) -> LeadActionLog:
    """Snapshot the state an archive action is about to overwrite. Caller is
    responsible for the commit (mirrors override_capture's contract)."""
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
    )
    db.add(row)
    return row


async def record_bucket_override_action(
    db: AsyncSession,
    *,
    lead: Lead,
    card: AssessmentCard,
    prior_bucket: str,
    prior_user_override: Optional[str],
    prior_user_override_at,
    prior_draft_type: Optional[str],
    prior_draft_subject: Optional[str],
    prior_draft_body: Optional[str],
    prior_draft_bucket: Optional[str],
    prior_rejection_reasons: Optional[list],
    new_bucket: str,
    prior_tags: Optional[list],
    actor_email: Optional[str],
) -> LeadActionLog:
    """Snapshot the assessment card fields a bucket-override action is about
    to overwrite, so /undo can restore bucket + override + draft together
    (issue #225). `new_bucket` is what the action sets the effective bucket
    to -- the router's staleness check compares it against the card's
    current effective bucket before allowing the undo. Caller commits."""
    row = LeadActionLog(
        lead_id=lead.id,
        action_type=ACTION_BUCKET_OVERRIDE,
        actor_email=actor_email,
        prior_state={
            "bucket": prior_bucket,
            "user_override": prior_user_override,
            "user_override_at": prior_user_override_at.isoformat() if prior_user_override_at else None,
            "draft_type": prior_draft_type,
            "draft_subject": prior_draft_subject,
            "draft_body": prior_draft_body,
            "draft_bucket": prior_draft_bucket,
            "rejection_reasons": prior_rejection_reasons,
            "new_bucket": new_bucket,
            "copper_id": lead.copper_id,
            "copper_tags": prior_tags or [],
        },
        email_sent=False,
        copper_outbox_id=None,
    )
    db.add(row)
    return row


async def _undo_bucket_override(db: AsyncSession, *, lead: Lead, action: LeadActionLog, card: AssessmentCard) -> dict:
    """Restores the assessment card's bucket/override/draft from `action`'s
    snapshot (issue #225) and mirrors the restored bucket tag back to Copper
    -- the counterpart to override_bucket's set_bucket_tag mirror, not
    reverse_archive_in_copper (a bucket-override auto-reject never wrote an
    Unqualified disposition, just a tag)."""
    prior = action.prior_state or {}
    prior_user_override_at = prior.get("user_override_at")

    card.bucket = prior.get("bucket")
    card.user_override = prior.get("user_override")
    card.user_override_at = datetime.fromisoformat(prior_user_override_at) if prior_user_override_at else None
    card.draft_type = prior.get("draft_type")
    card.draft_subject = prior.get("draft_subject")
    card.draft_body = prior.get("draft_body")
    card.draft_bucket = prior.get("draft_bucket")
    card.rejection_reasons = prior.get("rejection_reasons")
    action.undone_at = datetime.now(timezone.utc)

    copper_enqueued = False
    if lead.copper_id:
        try:
            copper_writer.set_bucket_tag(lead.copper_id, card.bucket, prior.get("copper_tags"))
            copper_enqueued = True
        except Exception as exc:
            print(f"[undo] Copper bucket-tag reversal failed (local commit succeeded): {exc!r}")

    await log_event(db, lead.id, EVENT_ACTION_UNDONE, {
        "reversed_action": action.action_type,
        "email_sent": False,
    })

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
    await db.refresh(card)

    return {
        "status": "undone",
        "action_type": action.action_type,
        "restored_bucket": card.bucket,
        "copper_enqueued": copper_enqueued,
        "email_sent": False,
    }


async def undo_action(
    db: AsyncSession, *, lead: Lead, action: LeadActionLog, card: Optional[AssessmentCard] = None,
) -> dict:
    """Reverses `action` against `lead`: restores lead.status from the
    snapshot, enqueues a Copper write-back reversing the status/tags/custom
    fields, marks the action + its training row consumed, and logs an
    `action_undone` event. Caller has already validated the action is
    undoable, unconsumed, and that `lead.status` still matches what the
    action produced (see the router).

    `action_type == ACTION_BUCKET_OVERRIDE` is a different shape entirely --
    it restores the assessment `card`'s bucket/override/draft instead of
    `lead.status` -- see _undo_bucket_override."""
    if action.action_type == ACTION_BUCKET_OVERRIDE:
        return await _undo_bucket_override(db, lead=lead, action=action, card=card)

    prior_state = action.prior_state or {}
    prior_status = prior_state.get("status") or "pending"
    prior_tags = prior_state.get("copper_tags")

    lead.status = prior_status
    action.undone_at = datetime.now(timezone.utc)

    copper_enqueued = False
    if lead.copper_id:
        try:
            new_outbox_id = copper_writer.reverse_archive_in_copper(
                lead.copper_id, prior_tags, pending_outbox_id=action.copper_outbox_id,
            )
            copper_enqueued = bool(new_outbox_id)
        except Exception as exc:
            print(f"[undo] Copper reversal failed (local commit succeeded): {exc!r}")

    await log_event(db, lead.id, EVENT_ACTION_UNDONE, {
        "reversed_action": action.action_type,
        "email_sent": action.email_sent,
    })

    # Don't poison the learning loop: the most recent not-yet-reverted
    # training row for this lead is the one this action produced (skip/approve
    # captured it right before or during the archive) -- mark it reverted so
    # it drops out of feedback_patterns.retrieve_labeled_exemplars.
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

    response = {
        "status": "undone",
        "action_type": action.action_type,
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
