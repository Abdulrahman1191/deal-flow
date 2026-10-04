"""
Eligibility gate for POST /leads/bulk-send-rejection (issue #205).

One function, used by all three call sites that need it -- the preview, the
send endpoint's re-check, and the per-lead Celery task's own re-check -- so
"eligible" can never mean something subtly different in one place than
another. Excludes, never "fixes": an ineligible lead is reported with a
reason and skipped, never auto-regenerated or auto-corrected mid-batch.
"""
from __future__ import annotations
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentCard
from app.models.lead import Lead

REASON_INVALID_LEAD_ID = "invalid_lead_id"
REASON_NOT_FOUND = "not_found"
REASON_NO_ASSESSMENT = "no_assessment"
REASON_NOT_REJECT_BUCKET = "not_reject_bucket"
REASON_ALREADY_SENT = "already_sent"
REASON_NO_RECIPIENT = "no_recipient_email"
REASON_STALE_OR_MISSING_DRAFT = "stale_or_missing_draft"
REASON_ARCHIVED_OR_CONVERTED = "lead_archived_or_converted"


def recipient_email(lead: Lead) -> Optional[str]:
    raw = lead.raw_copper_data or {}
    return raw.get("recipient_email") or None


def eligibility_reason(lead: Lead, card: Optional[AssessmentCard]) -> Optional[str]:
    """None when `lead` may be sent a bulk rejection right now, else a short
    machine-readable reason string. Order follows the issue's spec: bucket,
    already-sent, recipient, stale/missing draft (the issue #150 guard --
    `draft_bucket` must match the *current* effective bucket), then
    archived/converted."""
    if not card:
        return REASON_NO_ASSESSMENT
    effective_bucket = card.user_override or card.bucket
    if effective_bucket != "REJECT":
        return REASON_NOT_REJECT_BUCKET
    if card.sent_at:
        return REASON_ALREADY_SENT
    if not recipient_email(lead):
        return REASON_NO_RECIPIENT
    if not card.draft_body or card.draft_type != "rejection" or card.draft_bucket != effective_bucket:
        return REASON_STALE_OR_MISSING_DRAFT
    if lead.status == "archived" or lead.copper_opportunity_id:
        return REASON_ARCHIVED_OR_CONVERTED
    return None


async def load_lead_and_card(
    db: AsyncSession, raw_lead_id: str, owner_email: str
) -> tuple[Optional[Lead], Optional[AssessmentCard], Optional[str]]:
    """Fetch a lead (scoped to `owner_email`) plus its latest assessment
    card. Returns (None, None, reason) when `raw_lead_id` is malformed or
    doesn't resolve to one of the caller's own leads -- the same "not found,
    not someone else's" shape used throughout leads.py/assessments.py."""
    try:
        lead_uuid = uuid.UUID(str(raw_lead_id))
    except ValueError:
        return None, None, REASON_INVALID_LEAD_ID

    result = await db.execute(select(Lead).where(Lead.id == lead_uuid, Lead.owner_email == owner_email))
    lead = result.scalar_one_or_none()
    if not lead:
        return None, None, REASON_NOT_FOUND

    card_result = await db.execute(
        select(AssessmentCard)
        .where(AssessmentCard.lead_id == lead.id)
        .order_by(AssessmentCard.created_at.desc())
        .limit(1)
    )
    card = card_result.scalar_one_or_none()
    return lead, card, None
