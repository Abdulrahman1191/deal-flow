from __future__ import annotations
"""
Records what every DeepSeek call costs (issue #191), written from
app.services.claude_agent._chat_completion -- the single choke point every
DeepSeek call goes through.

Writes are sync and best-effort, same discipline as task_heartbeat and
assess_lead._mark_failed: `_chat_completion` runs synchronously inside an
already-running asyncio loop (both Celery tasks and FastAPI handlers call it
directly, not via await), so this cannot use the app's async engine without
nesting a second loop. It reuses the sync-engine-from-the-async-DATABASE_URL
helper copper_writer._psycopg2_url_and_connect_args already established for
exactly this situation. A failed write must never fail the call it measures,
so every exception is swallowed here, not surfaced to the caller.
"""
import uuid
from typing import Optional

from sqlalchemy import create_engine, text

from app.config import settings
from app.services import copper_writer

_LABEL = "llm_usage"


def record(
    *,
    purpose: str,
    status: str,
    model: str,
    duration_ms: int,
    lead_id: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """Store one DeepSeek call's cost + outcome. Never raises."""
    try:
        url, connect_args = copper_writer._psycopg2_url_and_connect_args(settings.database_url)
        engine = create_engine(url, connect_args=connect_args)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO llm_usage "
                    "(id, purpose, lead_id, model, prompt_tokens, completion_tokens, "
                    "total_tokens, duration_ms, status) "
                    "VALUES (:id, :purpose, :lead_id, :model, :prompt_tokens, "
                    ":completion_tokens, :total_tokens, :duration_ms, :status)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "purpose": purpose,
                    "lead_id": lead_id,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "duration_ms": duration_ms,
                    "status": status,
                },
            )
    except Exception as exc:
        print(f"[{_LABEL}] could not record usage (purpose={purpose} status={status}): {exc!r}")
