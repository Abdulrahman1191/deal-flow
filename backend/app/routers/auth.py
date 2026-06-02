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
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.models.user import User
from app.services.auth import get_current_user, is_owner

router = APIRouter(prefix="/auth", tags=["auth"])

# Where to send users if a legacy client hits /auth/login.
_PLATFORM_LOGIN_URL = "https://auth.apps.raed.vc"


class _MeOut(BaseModel):
    email: str
    name: str
    is_owner: bool


@router.get("/me", response_model=_MeOut)
async def me(request: Request, user: User = Depends(get_current_user)) -> _MeOut:
    """Echo who the platform proxy says is logged in. Useful for the frontend
    to show 'signed in as X' chips and for owner-gated UI."""
    display_name = (
        request.headers.get("X-Auth-Name")
        or request.headers.get("x-auth-name")
        or user.email
    )
    return _MeOut(email=user.email, name=display_name, is_owner=is_owner(user))


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
