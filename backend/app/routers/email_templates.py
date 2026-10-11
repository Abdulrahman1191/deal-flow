"""
Admin-gated read/write endpoints for the database-backed rejection email
templates (issue #234). Gated behind the same `is_owner` check the Reassign
page uses -- editing the copy every REJECT email is built from is an admin
action, not a per-partner one.

No rendering endpoint lives here: rendering happens at send time through
app.services.email_templates.render_rejection_email, called from
app.routers.assessments.render_rejection_template.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.email_template import EmailTemplate, EmailTemplateVersion
from app.models.user import User
from app.services import email_templates as email_templates_service
from app.services.auth import get_current_user, is_owner
from app.services.rejection_templates import TEMPLATE_KEYS

router = APIRouter(prefix="/email-templates", tags=["email-templates"])


def _owner_or_403(user: User) -> None:
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")


def _validate_key(language: str, template_key: str) -> None:
    if language not in email_templates_service.LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"language must be one of {email_templates_service.LANGUAGES}, got {language!r}",
        )
    if template_key not in TEMPLATE_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"template_key must be one of {TEMPLATE_KEYS}, got {template_key!r}",
        )


class EmailTemplateOut(BaseModel):
    id: str
    language: str
    template_key: str
    subject: str
    body: str
    reason_text: str
    updated_by: Optional[str]
    updated_at: datetime

    @classmethod
    def from_row(cls, row: EmailTemplate) -> "EmailTemplateOut":
        return cls(
            id=str(row.id),
            language=row.language,
            template_key=row.template_key,
            subject=row.subject,
            body=row.body,
            reason_text=row.reason_text,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
        )


class EmailTemplateVersionOut(BaseModel):
    id: str
    subject: str
    body: str
    reason_text: str
    saved_by: Optional[str]
    saved_at: datetime

    @classmethod
    def from_row(cls, row: EmailTemplateVersion) -> "EmailTemplateVersionOut":
        return cls(
            id=str(row.id),
            subject=row.subject,
            body=row.body,
            reason_text=row.reason_text,
            saved_by=row.saved_by,
            saved_at=row.saved_at,
        )


class EmailTemplateUpdateIn(BaseModel):
    subject: str
    body: str
    reason_text: str


@router.get("", response_model=list[EmailTemplateOut])
async def list_email_templates(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """All templates across both languages and all four keys."""
    _owner_or_403(user)
    rows = await email_templates_service.list_templates(db)
    return [EmailTemplateOut.from_row(r) for r in rows]


@router.put("/{language}/{template_key}", response_model=EmailTemplateOut)
async def update_email_template(
    language: str,
    template_key: str,
    body: EmailTemplateUpdateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    _validate_key(language, template_key)
    try:
        row = await email_templates_service.update_template(
            db,
            language=language,
            template_key=template_key,
            subject=body.subject,
            body=body.body,
            reason_text=body.reason_text,
            updated_by=user.email,
        )
    except email_templates_service.TemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return EmailTemplateOut.from_row(row)


@router.get("/{language}/{template_key}/versions", response_model=list[EmailTemplateVersionOut])
async def list_email_template_versions(
    language: str,
    template_key: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    _validate_key(language, template_key)
    rows = await email_templates_service.list_versions(db, language=language, template_key=template_key)
    return [EmailTemplateVersionOut.from_row(r) for r in rows]


@router.post("/{language}/{template_key}/revert/{version_id}", response_model=EmailTemplateOut)
async def revert_email_template(
    language: str,
    template_key: str,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owner_or_403(user)
    _validate_key(language, template_key)
    try:
        row = await email_templates_service.revert_template(
            db,
            language=language,
            template_key=template_key,
            version_id=version_id,
            updated_by=user.email,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return EmailTemplateOut.from_row(row)
