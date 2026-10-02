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
import logging
import os
import re
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

logger = logging.getLogger(__name__)


_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# The only (method, path) pairs a service-token caller (Reem) may use. Ids are
# UUID-only so /leads/export, /leads/orphans, /leads/failed-summary etc. never
# match /leads/{id}. Anything that queues founder email or Copper writes
# (/approve, /send, /mark-sent, /bulk-archive, ...) is deliberately absent.
SERVICE_ROUTES = (
    ("GET", re.compile(r"^/api/v1/leads$")),
    ("GET", re.compile(rf"^/api/v1/leads/{_UUID}$")),
    ("GET", re.compile(rf"^/api/v1/assessments/{_UUID}$")),
    ("POST", re.compile(rf"^/api/v1/assessments/{_UUID}/rate$")),
)
SERVICE_TOKEN_HEADER = "X-Service-Token"
PROXY_SECRET_HEADER = "X-Raed-Proxy"
SERVICE_EMAIL_DOMAIN = "@raed.vc"


def _matches(secret: str, presented: Optional[str]) -> bool:
    return bool(secret) and presented is not None and hmac.compare_digest(
        presented.encode(), secret.encode()
    )


def caller_gate(request: Request) -> Optional[tuple[int, str]]:
    """Decide whether a request may assert X-Auth-Email. Returns None to let
    it through, or (status, detail) to reject it before any route runs.

    X-Auth-Email used to be trusted from anything on the raed_platform
    network, i.e. every analyst prototype could act as any partner. Now:
    - X-Service-Token present: must equal DEALFLOW_SERVICE_TOKEN (401), the
      route must be in SERVICE_ROUTES (403), and the asserted X-Auth-Email
      must be an @raed.vc address (403).
    - Otherwise, when RAED_PROXY_SECRET is set, a request carrying
      X-Auth-Email must also carry the proxy's X-Raed-Proxy secret (401).
    Requests without X-Auth-Email (Copper ingest, health) are unaffected; they
    authenticate on their own or need no identity."""
    token = request.headers.get(SERVICE_TOKEN_HEADER)
    if token is not None:
        if not _matches(settings.dealflow_service_token, token):
            return 401, "Invalid service token"
        method, path = request.method.upper(), request.url.path
        if not any(m == method and rx.match(path) for m, rx in SERVICE_ROUTES):
            return 403, f"Service token is not allowed on {method} {path}"
        email = (request.headers.get("X-Auth-Email") or "").strip().lower()
        if not email.endswith(SERVICE_EMAIL_DOMAIN) or email == SERVICE_EMAIL_DOMAIN:
            return 403, "Service calls must act as an @raed.vc user (X-Auth-Email)"
        return None

    if settings.raed_proxy_secret and request.headers.get("X-Auth-Email"):
        if not _matches(settings.raed_proxy_secret, request.headers.get(PROXY_SECRET_HEADER)):
            return 401, "X-Auth-Email is only accepted from the platform proxy or with a service token"
    return None


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

    # First-contact: auto-create a user row. caller_gate (app/main.py) has
    # already checked who may assert X-Auth-Email, so a brand-new email = a
    # brand-new Raed teammate (or Reem's QA seat).
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


def _requested_view_as(request: Request) -> str:
    return (request.query_params.get("view_as") or "").strip().lower()


def effective_owner_email(request: Request, user: User) -> str:
    """The email whose board a scoped read should return.

    Admin-only "view as" QA mode (issue #50): if the caller is an admin
    (`is_owner`) and passes a non-empty `?view_as=`, scoped reads use that
    email instead of the caller's own. A non-admin passing `view_as` is
    silently ignored — falls back to their own email, never honored, never
    leaks. Every honored view_as is audit-logged here since this is the
    single choke point all scoped reads go through.
    """
    view_as = _requested_view_as(request)
    if view_as and is_owner(user):
        logger.info("view_as honored: admin=%s viewed=%s", user.email, view_as)
        return view_as
    return user.email


def is_impersonating(request: Request, user: User) -> bool:
    """True when a `view_as` query param is actually being honored (i.e. the
    caller is an admin and passed a non-empty view_as)."""
    return bool(_requested_view_as(request)) and is_owner(user)


def block_if_impersonating(request: Request, user: User) -> None:
    """Guard for mutating endpoints: the "view as" QA mode is read-only by
    design, so any mutation attempted while impersonating is refused."""
    if is_impersonating(request, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read-only while viewing another user's board",
        )


COPPER_WEBHOOK_TOKEN_HEADER = "X-Copper-Webhook-Token"


def verify_webhook_signature(payload: bytes, signature: str, token: str = "") -> bool:
    """Shared-secret verification for Copper webhook ingestion. Independent of
    user auth — the inbound webhook from Copper has no Slack identity.

    Copper cannot sign notifications: it has no HMAC support, it only replays
    the static `headers` configured on the subscription. So the path real
    Copper traffic takes is `token` (the X-Copper-Webhook-Token header that
    scripts/register_copper_webhooks.py registers) compared in constant time to
    COPPER_WEBHOOK_SECRET. An HMAC-SHA256 of the body in X-Copper-Signature is
    still accepted for callers that can sign. Fails closed: no secret
    configured, or neither credential valid, is a rejection."""
    secret = settings.copper_webhook_secret
    if not secret:
        return False
    if token and hmac.compare_digest(token.encode(), secret.encode()):
        return True
    if not signature:
        return False
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
