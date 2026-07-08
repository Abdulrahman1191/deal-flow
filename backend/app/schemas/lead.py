import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel
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
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadWithAssessment(LeadOut):
    assessment: Optional[AssessmentOut] = None

    model_config = {"from_attributes": True}


class PaginatedLeads(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[LeadWithAssessment]
