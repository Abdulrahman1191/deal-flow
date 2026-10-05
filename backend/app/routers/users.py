"""
Team-list endpoints — admin-only rosters built from
settings.client_facing_email_list() (TEAM_EMAILS minus
NON_CLIENT_FACING_EMAILS, config.py).

/team powers the "view as" navbar dropdown (issue #52): it excludes the
caller, since an admin can't view-as themselves. /roster (issue #214) is for
pages like Reassign Leads where the signed-in admin must be selectable as
both a source and a target, so it includes the caller and attaches each
teammate's display name from the `User` row when one exists.

Data-driven roster (issue #127): a teammate added to TEAM_EMAILS appears here
automatically; non-client-facing test/engineer accounts stay valid users but
are excluded from this list. Read-only; unrelated to the view_as read/guard
machinery in app.services.auth (#50), which is unchanged here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/users", tags=["users"])


class RosterEntry(BaseModel):
    email: str
    name: str | None = None


@router.get("/team", response_model=list[str])
async def team(user: User = Depends(get_current_user)) -> list[str]:
    """Client-facing teammate emails for the admin 'view as' dropdown,
    excluding the caller. Admin-only — reuses the same ADMIN_EMAILS gate as
    the other admin tabs."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    self_email = user.email.strip().lower()
    return [e for e in settings.client_facing_email_list() if e != self_email]


@router.get("/roster", response_model=list[RosterEntry])
async def roster(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RosterEntry]:
    """Full client-facing teammate roster, including the caller, each entry
    carrying the display name from the `User` row when one exists. For pages
    where the signed-in admin must be able to act on their own leads too
    (e.g. Reassign Leads, issue #214) — unlike /team, this never excludes
    self. Admin-only — same ADMIN_EMAILS gate as /team."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    emails = settings.client_facing_email_list()
    result = await db.execute(select(User).where(User.email.in_(emails)))
    names = {u.email: u.full_name for u in result.scalars().all()}
    return [RosterEntry(email=email, name=names.get(email)) for email in emails]
