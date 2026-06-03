import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


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
    research_sources: Optional[List]
    user_override: Optional[str]
    user_override_at: Optional[datetime]
    user_rating: Optional[str]
    user_rating_at: Optional[datetime]
    approved_at: Optional[datetime]
    sent_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


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
