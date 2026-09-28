from __future__ import annotations
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DuplicateLeadOut(BaseModel):
    id: uuid.UUID
    company_name: str
    owner_email: Optional[str] = None
    status: str
    copper_id: Optional[str] = None
    created_at: datetime
    applicant_email: Optional[str] = None

    model_config = {"from_attributes": True}


class DuplicateClusterOut(BaseModel):
    cluster_key: str
    match_reasons: List[str]
    signal_count: int
    leads: List[DuplicateLeadOut]


class DismissRequest(BaseModel):
    lead_ids: List[uuid.UUID] = Field(min_length=2)


class DismissResult(BaseModel):
    cluster_key: str
    dismissed_by: str
    dismissed_at: datetime


class ReassignRequest(BaseModel):
    lead_id: uuid.UUID
    owner_email: str


class ReassignResult(BaseModel):
    lead_id: uuid.UUID
    from_owner: Optional[str] = None
    to_owner: str
    copper_push_enqueued: bool
