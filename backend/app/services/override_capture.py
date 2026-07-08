"""
Override-capture service.

One call site per trigger (override / approve / skip). Snapshots the full
(AI input, AI output, human decision) tuple into `assessment_overrides` for
future LLM training/eval work.

Why this lives in its own service:
  - keeps router code clean (one line per call site)
  - centralises the "what does a training example look like" definition
  - failures in capture must never break the user-facing action; everything
    is wrapped in try/except with print logging
"""
from __future__ import annotations
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.models.override import AssessmentOverride


Trigger = Literal[
    "override", "re-override", "approve", "skip", "send",
    "confirm",    # thumbs-up: human agrees with the AI bucket
    "rate_down",  # thumbs-down: human disagrees with the AI bucket
]

# Cap pitch deck text at the same length the LLM sees — keeps row size bounded
# and matches what was actually presented to the model at assessment time.
_DECK_EXCERPT_CAP = 12_000


async def capture_override(
    db: AsyncSession,
    *,
    lead: Lead,
    card: AssessmentCard,
    human_bucket: str,
    trigger: Trigger,
    reason_tags: list[str] | None = None,
    reason: str | None = None,
    ai_bucket: str | None = None,
    acted_by_email: str | None = None,
) -> None:
    """Snapshot the current (AI call, human decision) pair.

    Called from the router after the user action has been committed. Wraps any
    exception in a print — capture failures must never block the user-facing
    action.

    `reason_tags` and `reason` are optional and may be None when the human
    didn't supply them (e.g. they hit Skip on the reason modal, or this is an
    auto-capture from approve/skip with no UI prompt).

    `ai_bucket` lets the caller record the AI's *original* call explicitly. This
    matters for the override flow, where `card.bucket` has already been updated
    to the human's choice — passing the pre-override bucket here keeps the
    training row honest without the caller having to mutate the live ORM object.
    Defaults to `card.bucket` when not provided (correct for approve/skip/rate).
    """
    try:
        row = AssessmentOverride(
            lead_id=lead.id,
            assessment_id=card.id,
            ai_bucket=ai_bucket if ai_bucket is not None else card.bucket,
            ai_confidence=card.confidence_score,
            ai_summary=card.summary,
            ai_breakdown=card.scoring_breakdown,
            human_bucket=human_bucket,
            trigger=trigger,
            research_snap=card.research_data,
            deck_excerpt=(lead.pitch_deck_text or "")[:_DECK_EXCERPT_CAP] or None,
            human_reason_tags=reason_tags or None,
            human_reason=(reason or "").strip() or None,
            acted_by_email=acted_by_email,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        # Never fail the user action because capture broke.
        print(f"[override_capture] failed for lead={lead.id} trigger={trigger}: {exc!r}")
