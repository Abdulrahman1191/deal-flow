import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, field_validator

from app.services.claude_agent import normalize_signal


def _sanitize_signal_list(items: Optional[List]) -> Optional[List]:
    """Serializer-side backstop for `positive_signals`/`red_flags` (issue
    #200): reads every entry through the one shared `normalize_signal`
    helper, so the API never hands a client a malformed item even if
    something bypassed claude_agent's own post-response normalisation (e.g.
    an older row, a script-written test fixture). Well-formed strings and
    {label, text} objects round-trip unchanged; a malformed entry degrades to
    its plain-text form rather than raising.
    """
    if not items:
        return items
    sanitized = []
    for item in items:
        label, text = normalize_signal(item)
        sanitized.append({"label": label, "text": text} if label else text)
    return sanitized


class AssessmentOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    bucket: str
    confidence_score: int
    summary: Optional[str]
    positive_signals: Optional[List]
    red_flags: Optional[List]
    data_gaps: Optional[List]
    scoring_breakdown: Optional[dict]
    draft_subject: Optional[str]
    draft_body: Optional[str]
    draft_type: Optional[str]
    # Defaults to None (unlike the other fields above) so pre-existing card
    # objects/test doubles built before this column existed (issue #150)
    # don't need updating just to satisfy response serialization.
    draft_bucket: Optional[str] = None
    # Computed, not stored (issue #177) -- see app.services.language_audit.
    # Default False so the field never breaks serialization of a card object
    # the router didn't explicitly compute it for (e.g. a nested read
    # elsewhere); every /assessments/* endpoint that returns a card sets the
    # real values before returning.
    language_mismatch: bool = False
    draft_missing: bool = False
    research_sources: Optional[List]
    assessed_without_deck: bool
    user_override: Optional[str]
    user_override_at: Optional[datetime]
    user_rating: Optional[str]
    user_rating_at: Optional[datetime]
    approved_at: Optional[datetime]
    sent_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("positive_signals", "red_flags", mode="before")
    @classmethod
    def _normalize_signals(cls, v: Optional[List]) -> Optional[List]:
        return _sanitize_signal_list(v)


class DraftUpdate(BaseModel):
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None


class BucketOverride(BaseModel):
    bucket: str
    # Optional human reason captured by the post-click ReasonModal.
    # Used as training-data context for prompt/few-shot tuning.
    reason_tags: Optional[List[str]] = None
    reason: Optional[str] = None


class AssessmentRating(BaseModel):
    """Thumbs up/down on the AI's recommendation (distinct from a bucket override).

    "up"   = human confirms the AI got the bucket right.
    "down" = human disagrees with the AI's bucket.
    Both register a training row; `reason`/`reason_tags` are optional context
    (the UI only prompts for them on a thumbs-down).
    """
    rating: str  # "up" | "down"
    reason_tags: Optional[List[str]] = None
    reason: Optional[str] = None
