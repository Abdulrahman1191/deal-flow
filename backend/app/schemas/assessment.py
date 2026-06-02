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
