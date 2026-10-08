"""
Shared circuit breaker for the DeepSeek API.

A 402 (account out of balance) or 401 (bad/revoked key) is account-wide: every
later call fails the same way until a human tops up or rotates the key. Retrying
per lead turned the 2026-09-30 balance outage into ~51k failing calls a day
(sync_pitch_decks re-verifying every unmatched deck every 30 minutes) and
burned assess_lead attempts/redrives on leads that had nothing wrong with them.

So one such response opens the breaker for settings.llm_outage_pause_seconds,
shared through Redis by the app, worker and worker-heavy containers. While it
is open, claude_agent raises LLMUnavailable without calling DeepSeek. When the
key expires, the next call is the probe: it either succeeds (closed again) or
re-opens the breaker for another pause. If Redis itself is unreachable the
breaker falls back to this process's own memory, so it still protects the
process that saw the 402.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone

import redis

from app.config import settings
from app.services import ops_alert

ACCOUNT_LEVEL_STATUSES = (401, 402)
_KEY = "llm:deepseek:circuit_open"
# Same TTL as _KEY, written alongside it -- lets GET /ops/queues report when
# the breaker opened, not just that it's open.
_OPENED_AT_KEY = "llm:deepseek:circuit_opened_at"

_redis = None
_local_open_until = 0.0
_local_opened_at: str | None = None


class LLMUnavailable(RuntimeError):
    """DeepSeek is unusable account-wide (no balance / bad key). Not a problem
    with the lead or prompt: callers should pause, not retry or fail the item."""


def _client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(settings.redis_url)
    return _redis


def _page_for(reason: str, seconds: int) -> None:
    """Tell someone, once per outage: the breaker going from closed to open. The
    re-opens after each failed probe are the same outage; reem's per-key cooldown
    keeps them from paging again."""
    minutes = max(round(seconds / 60), 1)
    if "401" in reason.split(":", 1)[0]:
        action = (f"deal-flow paused its AI assessments: DeepSeek rejected the API key (401). "
                  f"Put a valid key in deal-flow's DEEP_SEEK_API and restart deal-flow-app and both workers; "
                  f"it retries on its own every {minutes} minutes.")
    else:
        action = (f"deal-flow paused its AI assessments: DeepSeek says the account is out of balance (402). "
                  f"Top up the DeepSeek balance; deal-flow retries on its own every {minutes} minutes and resumes then.")
    ops_alert.page("deal-flow-deepseek-paused", action,
                   f"{reason}. New and pending leads wait in the queue; none are marked failed.")


def open_breaker(reason: str) -> None:
    global _local_open_until, _local_opened_at
    was_open = open_reason() is not None
    seconds = max(int(settings.llm_outage_pause_seconds), 1)
    _local_open_until = time.monotonic() + seconds
    _local_opened_at = datetime.now(timezone.utc).isoformat()
    try:
        client = _client()
        client.set(_KEY, reason, ex=seconds)
        client.set(_OPENED_AT_KEY, _local_opened_at, ex=seconds)
    except Exception as exc:
        print(f"[llm_breaker] redis unavailable, breaker local to this process: {exc!r}")
    print(f"[llm_breaker] OPEN for {seconds}s: {reason}")
    if not was_open:
        _page_for(reason, seconds)


def open_reason() -> str | None:
    """The reason the breaker is open, or None when DeepSeek may be called."""
    try:
        value = _client().get(_KEY)
        if value is not None:
            return value.decode() if isinstance(value, bytes) else str(value)
        return None
    except Exception:
        if time.monotonic() < _local_open_until:
            return "DeepSeek paused (local breaker; redis unavailable)"
        return None


def opened_at() -> str | None:
    """ISO timestamp of when the currently-open breaker was opened, or None
    once it has closed. Used by GET /ops/queues -- see open_reason()."""
    try:
        value = _client().get(_OPENED_AT_KEY)
        if value is not None:
            return value.decode() if isinstance(value, bytes) else str(value)
        return None
    except Exception:
        if time.monotonic() < _local_open_until:
            return _local_opened_at
        return None


def check() -> None:
    """Raise LLMUnavailable if the breaker is open."""
    reason = open_reason()
    if reason is not None:
        raise LLMUnavailable(reason)
