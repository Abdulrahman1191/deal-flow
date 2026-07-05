"""
Auth router — vestigial after the platform migration.

The platform handles Slack-OTP login at the proxy layer, so /auth/login and
/auth/register are no longer needed and would be confusing if left in place.

We keep two endpoints:
  - GET  /auth/me     — convenience alias for /api/v1/me (the actual user-info
                        endpoint lives in main.py). Frontend usually calls
                        /api/v1/me directly; this exists so legacy clients
                        don't 404.
  - POST /auth/login  — returns 410 Gone with a clear message pointing to the
                        platform login URL, in case anything still tries.
  - POST /auth/register — 410 Gone (registration is via Slack, not us).
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/auth", tags=["auth"])

# Where to send users if a legacy client hits /auth/login.
_PLATFORM_LOGIN_URL = "https://auth.apps.raed.vc"


class _MeOut(BaseModel):
    email: str
    name: str
    is_owner: bool
    # First-run onboarding: false until the user finishes the welcome flow.
    onboarded: bool
    # Whether we've resolved this user's Copper account (drives the onboarding
    # "no Copper deals" fallback messaging).
    copper_linked: bool


@router.get("/me", response_model=_MeOut)
async def me(request: Request, user: User = Depends(get_current_user)) -> _MeOut:
    """Echo who the platform proxy says is logged in. Useful for the frontend
    to show 'signed in as X' chips and to drive owner-gated UI / onboarding."""
    display_name = (
        request.headers.get("X-Auth-Name")
        or request.headers.get("x-auth-name")
        or user.email
    )
    return _MeOut(
        email=user.email,
        name=display_name,
        is_owner=is_owner(user),
        onboarded=user.onboarded_at is not None,
        copper_linked=user.copper_user_id is not None,
    )


@router.post("/onboard", response_model=_MeOut)
async def complete_onboarding(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> _MeOut:
    """Mark the first-run onboarding as complete (idempotent)."""
    if user.onboarded_at is None:
        user.onboarded_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
    display_name = (
        request.headers.get("X-Auth-Name")
        or request.headers.get("x-auth-name")
        or user.email
    )
    return _MeOut(
        email=user.email,
        name=display_name,
        is_owner=is_owner(user),
        onboarded=True,
        copper_linked=user.copper_user_id is not None,
    )


@router.post("/login", status_code=410)
async def login_gone():
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "message": "Login is handled by the Raed platform proxy (Slack OTP).",
            "platform_login_url": _PLATFORM_LOGIN_URL,
        },
    )


@router.post("/register", status_code=410)
async def register_gone():
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Registration is via @raed.vc Slack, not this app.",
    )
