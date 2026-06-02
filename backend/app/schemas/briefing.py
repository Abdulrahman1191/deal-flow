import uuid
from datetime import datetime, date
from typing import List

from pydantic import BaseModel


class BriefingOut(BaseModel):
    id: uuid.UUID
    date: date
    top_themes: List
    deep_dives: List
    generated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedBriefings(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[BriefingOut]
