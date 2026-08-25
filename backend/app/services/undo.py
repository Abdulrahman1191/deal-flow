from __future__ import annotations
"""
Undo service (issue #153, narrowed to archive-only for this PR).

Two halves:
  - `record_archive_action` -- called from the archive call sites
    (leads.py::archive_no_reply, assessments.py::_finalize_sent's
    rejection-send branch) right after they've decided what changed, to
    snapshot the state they're about to overwrite.
  - `undo_action` -- called from POST /leads/{lead_id}/undo to restore that
    snapshot and reverse the Copper write-back.

Bucket-override/approve/bulk-archive undo, the frontend toast, and the
calibration-stats exclusion are deferred follow-ups -- see the issue.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.models.lead_action_log import LeadActionLog
from app.models.override import AssessmentOverride
from app.services import copper_writer
from app.services.events import EVENT_ACTION_UNDONE, log_event

ACTION_ARCHIVE_NO_REPLY = "archive_no_reply"
ACTION_ARCHIVE_AFTER_SEND = "archive_after_send"

# Action types POST /leads/{lead_id}/undo will reverse. Anything else logged
# to lead_action_log in the future (bucket override, approve, ...) is
# invisible to /undo until it's explicitly added here.
UNDOABLE_ACTIONS = {ACTION_ARCHIVE_NO_REPLY, ACTION_ARCHIVE_AFTER_SEND}


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


async def undo_action(db: AsyncSession, *, lead: Lead, action: LeadActionLog) -> dict:
    """Reverses `action` against `lead`: restores lead.status from the
    snapshot, enqueues a Copper write-back reversing the status/tags/custom
    fields, marks the action + its training row consumed, and logs an
    `action_undone` event. Caller has already validated the action is
    undoable, unconsumed, and that `lead.status` still matches what the
    action produced (see the router)."""
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
