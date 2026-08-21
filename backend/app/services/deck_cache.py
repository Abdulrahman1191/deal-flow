from __future__ import annotations
"""
Cache of extracted pitch-deck text, keyed by Drive file id + modifiedTime.

Why this exists: sync_pitch_decks' Drive-folder sweep downloads and re-extracts
a PDF on EVERY run (every 30 minutes) whenever the filename can't be resolved to
a single lead -- the match-verification tier needs the deck's text to pick
between candidates, and a file that never resolves is retried forever by design
("the next run's DB-driven remaining_leads query retries it").

With Tesseract behind that call it meant re-OCRing the same unchanged PDF at 300
DPI, forever, inside the single-slot worker. With the LLM tier it's an API call
per unresolved file per sweep instead -- cheaper, but still pure waste on a file
whose bytes have not changed.

A Drive file's (id, modifiedTime) pair identifies exact content, so a hit is
always safe. Empty extractions are cached too -- that's the whole point: a deck
that yields nothing is precisely the one we don't want to pay for again.

Entirely best-effort. Redis here is the Celery broker, configured without
persistence, so this is a cache and never a source of truth: any Redis problem
degrades to "extract it again" rather than failing the ingestion.
"""
from typing import Optional

from app.config import settings

# Long enough to kill the every-30-minutes retry loop, short enough that a
# genuinely unreadable deck is re-tried after a model/config change rather than
# being written off permanently.
CACHE_TTL_SECONDS = 7 * 24 * 3600

_PREFIX = "deck_extract:v1:"


def _client():
    """Return a Redis client, or None if unavailable. Never raises."""
    try:
        import redis

        return redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception as exc:
        print(f"[deck_cache] redis unavailable: {exc!r}")
        return None


def _key(file_id: str, modified_time: Optional[str]) -> str:
    # modifiedTime may be absent (some callers fetch only id+name). Falling back
    # to the bare id is still correct for the overwhelmingly common case of a
    # deck that is uploaded once and never edited in place.
    return f"{_PREFIX}{file_id}:{modified_time or '-'}"


def get(file_id: str, modified_time: Optional[str] = None) -> Optional[str]:
    """Cached text for this exact file version, or None if not cached.

    A cached empty extraction returns "" (a hit), which is distinct from None
    (a miss) -- callers must not treat the two the same.
    """
    client = _client()
    if client is None:
        return None
    try:
        return client.get(_key(file_id, modified_time))
    except Exception as exc:
        print(f"[deck_cache] read failed for {file_id}: {exc!r}")
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def put(file_id: str, text: str, modified_time: Optional[str] = None) -> None:
    """Store an extraction result. Failures are logged and ignored."""
    client = _client()
    if client is None:
        return
    try:
        client.setex(_key(file_id, modified_time), CACHE_TTL_SECONDS, text or "")
    except Exception as exc:
        print(f"[deck_cache] write failed for {file_id}: {exc!r}")
    finally:
        try:
            client.close()
        except Exception:
            pass


def invalidate(file_id: str, modified_time: Optional[str] = None) -> None:
    """Drop a cached extraction so the next sweep re-reads the deck.

    Used by scripts/reextract_pitch_decks.py, whose entire purpose is to force
    a fresh extraction pass over decks a previous tier failed on.
    """
    client = _client()
    if client is None:
        return
    try:
        if modified_time is None:
            for k in client.scan_iter(f"{_PREFIX}{file_id}:*"):
                client.delete(k)
        else:
            client.delete(_key(file_id, modified_time))
    except Exception as exc:
        print(f"[deck_cache] invalidate failed for {file_id}: {exc!r}")
    finally:
        try:
            client.close()
        except Exception:
            pass
