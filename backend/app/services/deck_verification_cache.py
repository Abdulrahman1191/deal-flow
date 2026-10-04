from __future__ import annotations
"""
Cache of deck-verification verdicts (issue #192 item 1).

pitch_deck.verify_match_candidates makes one DeepSeek call per near-miss
candidate, and sync_pitch_decks' Drive-folder sweep reruns every 30 minutes
over every file that hasn't matched a lead yet -- so without this cache the
same (Drive file, lead) pair is re-verified up to 48x/day forever, always
reaching the same verdict. The 2026-09-30 breaker outage (see
app/services/llm_breaker.py) traced ~51k failing calls/day largely to this
path.

Keyed on (drive_file_id, lead_id): a hit requires the stored deck-text hash
to still match (a file replaced in place under the same Drive id is a
genuinely new deck, not a repeat) and the row to be within
settings.deck_verification_cache_ttl_days.

Written via the same best-effort sync-engine pattern as
app/services/llm_usage.py -- verify_match_candidates runs synchronously
inside an already-running asyncio loop (both Celery tasks and the on-demand
sync-deck endpoint call it directly, not via await), so this can't use the
app's async engine without nesting a second loop. A failed read is treated
as a miss (degrades to "verify it again", never to a wrong verdict); a
failed write is logged and ignored.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import create_engine, text

from app.config import settings
from app.services import copper_writer

_LABEL = "deck_verification_cache"


def deck_text_hash(deck_text: str) -> str:
    return hashlib.sha256((deck_text or "").encode("utf-8")).hexdigest()


def _sync_engine():
    url, connect_args = copper_writer._psycopg2_url_and_connect_args(settings.database_url)
    return create_engine(url, connect_args=connect_args)


def get(drive_file_id: str, lead_id: str, text_hash: str) -> Optional[bool]:
    """Cached verdict for this exact (file, lead, deck-text) triple.

    None means "miss" -- no row, a changed deck-text hash, or an expired
    TTL -- in every case the caller must re-verify via the LLM.
    """
    try:
        engine = _sync_engine()
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.deck_verification_cache_ttl_days)
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT is_match, deck_text_hash, verified_at FROM deck_verification_cache "
                    "WHERE drive_file_id = :drive_file_id AND lead_id = :lead_id"
                ),
                {"drive_file_id": drive_file_id, "lead_id": lead_id},
            ).first()
    except Exception as exc:
        print(f"[{_LABEL}] read failed for drive_file_id={drive_file_id} lead_id={lead_id}: {exc!r}")
        return None

    if row is None:
        return None
    is_match, stored_hash, verified_at = row
    if stored_hash != text_hash or verified_at < cutoff:
        return None
    return bool(is_match)


def put(drive_file_id: str, lead_id: str, text_hash: str, is_match: bool) -> None:
    """Upsert this verdict. Failures are logged and ignored."""
    try:
        engine = _sync_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO deck_verification_cache "
                    "(id, drive_file_id, lead_id, deck_text_hash, is_match, verified_at) "
                    "VALUES (:id, :drive_file_id, :lead_id, :deck_text_hash, :is_match, now()) "
                    "ON CONFLICT (drive_file_id, lead_id) DO UPDATE SET "
                    "deck_text_hash = EXCLUDED.deck_text_hash, "
                    "is_match = EXCLUDED.is_match, "
                    "verified_at = EXCLUDED.verified_at"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "drive_file_id": drive_file_id,
                    "lead_id": lead_id,
                    "deck_text_hash": text_hash,
                    "is_match": is_match,
                },
            )
    except Exception as exc:
        print(f"[{_LABEL}] write failed for drive_file_id={drive_file_id} lead_id={lead_id}: {exc!r}")
