from __future__ import annotations
"""
Echo-loop guard for outbound writes to Copper.

Every outbound PUT/POST registers a Redis key with the payload hash. When Copper's webhook
fires back with an 'update' event for the same record, we match the incoming payload against
the registry; a hit means "we just wrote this — drop it".

Two layered defences:

  Defence #2 (in-flight registry): hash of the outbound payload vs. hash of the inbound
    updated_attributes; TTL 90s.
  Defence #3 (app-owned key allowlist): if every changed field is one we own (raed:* tags,
    Raed * custom fields, status_id when we wrote it), drop without needing a registry hit.

Defence #1 (API-user identity check) is deferred — Copper webhooks don't reliably carry the
modifier ID. We rely on #2 and #3.
"""
import hashlib
import json
from typing import Optional, Tuple

import redis

from app.config import settings

_redis: Optional[redis.Redis] = None


def _client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(settings.redis_url)
    return _redis


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def register_outbound_write(copper_id: str, payload: dict, ttl: int = 90) -> str:
    """Records that we're about to send `payload` to Copper for this copper_id."""
    h = _hash_payload(payload)
    key = f"copper:echo:{copper_id}:{h}"
    try:
        _client().setex(key, ttl, "1")
    except Exception as exc:
        # Redis hiccup must not block the outbound write.
        print(f"[echo_guard] register failed: {exc!r}")
    return h


def is_recent_echo(copper_id: str, updated_attributes: dict) -> Tuple[bool, str]:
    """
    Returns (drop?, reason). Two checks:
      1. The hashed inbound payload matches a recently-registered outbound payload.
      2. Every changed field is in our app-owned namespace.
    """
    if not copper_id or not updated_attributes:
        return False, ""

    # Defence #2: registry hit
    try:
        h = _hash_payload(updated_attributes)
        if _client().get(f"copper:echo:{copper_id}:{h}"):
            return True, "registry-hit"
    except Exception:
        pass

    # Defence #3: only app-owned keys changed
    if _all_app_owned(updated_attributes):
        return True, "app-owned-fields-only"

    return False, ""


def _all_app_owned(updated_attributes: dict) -> bool:
    """Returns True iff every changed field is one we own."""
    if not updated_attributes:
        return False

    for key, value in updated_attributes.items():
        if key == "tags":
            # value is typically [old_list, new_list]; consider the union of both
            tags_seen = []
            for side in (value or [[], []]):
                if isinstance(side, list):
                    tags_seen.extend(side)
                elif isinstance(side, str):
                    tags_seen.append(side)
            if any(not str(t).startswith("raed:") for t in tags_seen):
                return False
        elif key in ("date_last_contacted", "status_id"):
            # We may write these; treat as app-owned for echo purposes
            continue
        elif key == "custom_fields":
            # All custom-field writes from this app are app-owned by construction
            continue
        else:
            return False
    return True
