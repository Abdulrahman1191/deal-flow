"""
Team-list endpoint — powers the admin "view as" navbar dropdown (issue #52).

Exposes settings.client_facing_email_list() (TEAM_EMAILS minus
NON_CLIENT_FACING_EMAILS, config.py) via a tiny admin-only API so the
frontend can populate the dropdown without duplicating that parsing logic.
Data-driven roster (issue #127): a teammate added to TEAM_EMAILS appears here
automatically; non-client-facing test/engineer accounts stay valid users but
are excluded from this list. Read-only; unrelated to the view_as read/guard
machinery in app.services.auth (#50), which is unchanged here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/team", response_model=list[str])
async def team(user: User = Depends(get_current_user)) -> list[str]:
    """Client-facing teammate emails for the admin 'view as' dropdown,
    excluding the caller. Admin-only — reuses the same ADMIN_EMAILS gate as
    the other admin tabs."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    self_email = user.email.strip().lower()
    return [e for e in settings.client_facing_email_list() if e != self_email]
