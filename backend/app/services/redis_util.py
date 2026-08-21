from __future__ import annotations
"""
Shared best-effort Redis client for operational bookkeeping.

Redis here is the Celery broker, configured without persistence (see
docker-compose.yml: "No persistence — Celery queue state is OK to lose on
restart"). Everything built on this module must therefore treat a miss, an
outage or a wiped database as normal and degrade to "unknown", never to an
error and never to a behaviour change.
"""
from typing import Optional

from app.config import settings


def client(label: str) -> Optional["object"]:
    """A Redis client, or None if it can't be reached. Never raises.

    `label` only tags the log line so an outage is attributable to the caller.
    """
    try:
        import redis

        return redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception as exc:
        print(f"[{label}] redis unavailable: {exc!r}")
        return None
