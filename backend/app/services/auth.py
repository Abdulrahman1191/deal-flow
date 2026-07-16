"""
Auth — platform-proxy-trusted edition.

The platform handles Slack-OTP login at the reverse proxy. Every request that
reaches us has X-Auth-Email / X-Auth-Slack-Id / X-Auth-Name headers populated
by the proxy. Our job is to trust those headers, materialise a `users` row on
first contact, and gate owner-only features against settings.owner_email.

For local dev (NODE_ENV != production, or no platform proxy in front), the
querystring `?fake_email=foo@raed.vc` stands in for the X-Auth-Email header
— mirrors the convention in the platform starter.

Legacy `/auth/login` endpoint is gone — see auth.py router for the migration
path: a 410 Gone response that tells clients to redirect to the platform
login URL.
"""
from __future__ import annotations
import hashlib
import hmac
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User


# When True, accept ?fake_email=... as a stand-in for X-Auth-Email. Set this
# in your local .env (or Docker compose override) for development only.
#
# Fails CLOSED: the dev bypass requires ENV to be explicitly set to "dev".
# An unset or misconfigured ENV var (e.g. missing on the platform's deploy
# form, or a typo like "production") must never enable identity impersonation
# via a query string — see SECURITY_AUDIT.md finding F1.
_LOCAL_DEV = os.getenv("ENV", "prod").lower() == "dev"


def _extract_email(request: Request) -> Optional[str]:
    """Pull the authenticated email from the platform headers, with a local
    dev fallback. Returns None if the request isn't authenticated."""
    email = request.headers.get("X-Auth-Email") or request.headers.get("x-auth-email")
    if email:
        return email.strip().lower()
    if _LOCAL_DEV:
        fake = request.query_params.get("fake_email")
        if fake:
            return fake.strip().lower()
    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency. Returns the authenticated User, auto-creating
    on first contact. Raises 401 if no auth header / fake_email is present.

    The platform proxy gates *all* traffic before reaching us, so in production
    a missing X-Auth-Email indicates either (a) the request bypassed the
    proxy (defence in depth — refuse), or (b) we're running locally without
    the dev fallback querystring.
    """
    email = _extract_email(request)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No X-Auth-Email header. The platform proxy should be adding this; "
                   "for local dev, append ?fake_email=you@raed.vc to the URL.",
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is deactivated")
        return user

    # First-contact: auto-create a user row. We trust the platform proxy not
    # to forge X-Auth-Email, so a brand-new email = a brand-new Raed teammate.
    user = User(
        email=email,
        # The User model still has hashed_password as nullable=False for
        # backwards-compat with the migration history. Insert a deterministic
        # sentinel so we can spot "platform-created" users later.
        hashed_password="platform-managed",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def is_owner(user: User) -> bool:
    """True if this user has ADMIN access — Portfolio + Feedback + Overrides tabs.

    Restricted to the ADMIN_EMAILS allow-list (defaults to just `owner_email`).
    This gates the ADMIN tabs ONLY. Per-user LEAD visibility is enforced
    separately — leads are always scoped to `owner_email == user.email` — so a
    non-admin teammate still sees their own leads; they just don't get the admin
    tabs. Add teammates to ADMIN_EMAILS to grant them admin.
    """
    return user.email.strip().lower() in settings.admin_email_set()


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """HMAC verification for Copper webhook ingestion. Independent of user
    auth — the inbound webhook from Copper has no Slack identity and is
    gated on this shared-secret HMAC instead."""
    if not settings.copper_webhook_secret or not signature:
        return False
    expected = hmac.new(
        settings.copper_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
