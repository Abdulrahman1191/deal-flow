import uuid
from datetime import datetime, timezone
from typing import Literal, Optional, List

from pydantic import BaseModel, Field, computed_field
from app.schemas.assessment import AssessmentOut


class LeadIngest(BaseModel):
    copper_id: Optional[str] = None
    company_name: str
    website: Optional[str] = None
    description: Optional[str] = None
    stage: Optional[str] = None
    region: Optional[str] = None
    founder_names: Optional[List[str]] = None
    linkedin_urls: Optional[List[str]] = None
    raw_copper_data: Optional[dict] = None


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
    company_linkedin_url: Optional[str] = None


class LeadOut(BaseModel):
    id: uuid.UUID
    copper_id: Optional[str]
    owner_email: Optional[str]
    company_name: str
    website: Optional[str]
    description: Optional[str]
    stage: Optional[str]
    region: Optional[str]
    founder_names: Optional[List[str]]
    linkedin_urls: Optional[List[str]]
    company_linkedin_url: Optional[str]
    pitch_deck_filename: Optional[str] = None
    pitch_deck_ingested_at: Optional[datetime] = None
    pitch_deck_drive_id: Optional[str] = None
    # Prior-contact signal from Copper's activity feed (issue #90) -- null
    # until the first successful sync computes it.
    prior_contact: Optional[bool] = None
    prior_contact_count: Optional[int] = None
    prior_contact_last_at: Optional[datetime] = None
    # Persisted failure reason (issue #163) -- populated when status=='failed',
    # null otherwise (including after a subsequent successful assessment).
    last_assessment_error: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
    # Only used to derive applied_at below — never serialized to the API.
    raw_copper_data: Optional[dict] = Field(default=None, exclude=True)

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[misc]
    @property
    def applied_at(self) -> Optional[datetime]:
        """The lead's true application date from Copper, falling back to our
        import timestamp (created_at) when Copper's date isn't available —
        raw_copper_data["date_created"] is epoch seconds from Copper's API."""
        raw = self.raw_copper_data
        if isinstance(raw, dict):
            date_created = raw.get("date_created")
            if isinstance(date_created, (int, float)) and not isinstance(date_created, bool):
                try:
                    return datetime.fromtimestamp(date_created, tz=timezone.utc)
                except (ValueError, OSError, OverflowError):
                    pass
        return self.created_at


class LeadWithAssessment(LeadOut):
    assessment: Optional[AssessmentOut] = None

    model_config = {"from_attributes": True}


class PaginatedLeads(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[LeadWithAssessment]


class BulkArchiveRequest(BaseModel):
    lead_ids: List[str]


class BulkArchiveFailure(BaseModel):
    lead_id: str
    error: str


class BulkArchiveResult(BaseModel):
    archived: int
    copper_enqueued: int
    failed: List[BulkArchiveFailure]


class PitchDeckSyncResult(BaseModel):
    """Structured diagnostic returned by POST /leads/{id}/sync-pitch-deck --
    never a bare 500, so the "Fetch pitch deck" button can always show the
    user exactly why a deck did or didn't attach."""

    configured: bool
    folder_readable: bool
    files_in_folder: int = 0
    matched_file: Optional[str] = None
    closest_candidates: List[str] = Field(default_factory=list)
    attached: bool
    extracted_chars: int = 0
    garbled: bool = False
    reassessment_queued: bool = False
    reason: str


class BulkReassignPreviewRequest(BaseModel):
    from_owner: str
    to_owners: List[str] = Field(min_length=1)
    bucket: Optional[Literal["YES", "MAYBE", "REJECT"]] = None
    include_converted: bool = False


class BulkReassignRequest(BulkReassignPreviewRequest):
    # Must equal the preview's `count` -- a cheap guard against the board
    # shifting between preview and execute (409 on mismatch, nothing written).
    confirm_count: int


class BulkReassignPreviewResult(BaseModel):
    count: int
    by_status: dict[str, int]
    by_bucket: dict[str, int]
    by_target: dict[str, int]


class BulkReassignFailure(BaseModel):
    lead_id: str
    error: str


class BulkReassignResult(BaseModel):
    batch_id: uuid.UUID
    moved: int
    by_target: dict[str, int]
    failed: List[BulkReassignFailure]


class BulkSendRejectionPreviewRequest(BaseModel):
    lead_ids: List[str]


class BulkSendRejectionPreviewItem(BaseModel):
    lead_id: str
    company_name: Optional[str] = None
    recipient_email: Optional[str] = None
    draft_subject: Optional[str] = None
    draft_excerpt: Optional[str] = None
    eligible: bool
    reason: Optional[str] = None


class BulkSendRejectionPreviewResult(BaseModel):
    eligible_count: int
    items: List[BulkSendRejectionPreviewItem]


class BulkSendRejectionRequest(BaseModel):
    lead_ids: List[str]
    # Must equal the preview's eligible_count -- a cheap guard against the
    # board shifting (or the preview going stale) between preview and send
    # (409 on mismatch, nothing sent).
    confirm_count: int


class BulkSendRejectionSkipped(BaseModel):
    lead_id: str
    reason: str


class BulkSendRejectionResult(BaseModel):
    batch_id: uuid.UUID
    queued: int
    skipped: List[BulkSendRejectionSkipped]


class BulkSendRejectionBatchItemOut(BaseModel):
    lead_id: str
    company_name: Optional[str] = None
    status: str
    reason: Optional[str] = None

    model_config = {"from_attributes": True}


class BulkSendRejectionBatchStatus(BaseModel):
    batch_id: uuid.UUID
    sent: int
    failed: int
    skipped: int
    queued: int
    items: List[BulkSendRejectionBatchItemOut]
