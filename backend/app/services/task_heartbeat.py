from __future__ import annotations
"""
Last-run heartbeat for Celery tasks, recorded by signal handlers.

Why this exists: on 2026-08-20 PR #130 routed assess_lead/sync_pitch_decks to
the `heavy` queue while prod still ran one worker consuming only `default`.
Assessments stopped dead at 11:05. Nothing anywhere reported it -- the
containers were "Up", RestartCount was 0, CPU was 0.16%, `/health` returned
{"status":"ok","db":"ok"} throughout, and Copper sync kept working perfectly.
The outage was only found by hand, hours later, with `redis-cli LLEN heavy`.

A queue depth alone can't distinguish "nothing to do" from "nobody consuming",
so depth is not enough either. The pair (depth, last successful run of the task
that drains it) does distinguish them, and that is what this module supplies:
the last-run half.

Written from the worker via task_prerun/task_postrun signals (see
tasks/celery_app.py) so every task is covered automatically -- a task added
later cannot forget to report. Read by the API for GET /api/v1/ops/queues.

Best-effort by construction: Redis is the broker and has no persistence, so a
restart empties this and every task reads back as "unknown" until it next runs.
Unknown is an honest answer; a wrong timestamp would not be. Recording must
never affect task execution, so every path swallows its exceptions.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from app.services.redis_util import client

# Bumped if the value shape changes, so a new reader never parses old JSON.
HASH_KEY = "task_heartbeat:v1"

# Long enough that a daily task (dedupe-leads, daily-briefing) still shows its
# last run after a quiet weekend, and that a task which STOPPED running stays
# visibly stale instead of quietly disappearing from the report -- a missing
# row reads as "new", a stale row reads as "broken", and broken is the truth.
TTL_SECONDS = 14 * 24 * 3600

_LABEL = "task_heartbeat"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(
    task_name: str,
    *,
    state: str,
    runtime_seconds: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    """Store the outcome of one task run. Never raises."""
    c = client(_LABEL)
    if c is None:
        return
    payload = {
        "at": _now().isoformat(),
        "state": state,
        "runtime_seconds": round(runtime_seconds, 3) if runtime_seconds is not None else None,
        # Truncated: this is a status line, not a log. The full traceback is in
        # the worker log, which is where you go once this tells you where to look.
        "error": (error or "")[:300] or None,
    }
    try:
        c.hset(HASH_KEY, task_name, json.dumps(payload))
        c.expire(HASH_KEY, TTL_SECONDS)
    except Exception as exc:
        print(f"[{_LABEL}] could not record {task_name}: {exc!r}")
    finally:
        try:
            c.close()
        except Exception:
            pass


def read_all() -> dict:
    """{task_name: {at, state, runtime_seconds, error}}. {} if unavailable."""
    c = client(_LABEL)
    if c is None:
        return {}
    try:
        raw = c.hgetall(HASH_KEY) or {}
    except Exception as exc:
        print(f"[{_LABEL}] could not read heartbeats: {exc!r}")
        return {}
    finally:
        try:
            c.close()
        except Exception:
            pass

    out = {}
    for name, blob in raw.items():
        try:
            out[name] = json.loads(blob)
        except Exception:
            # A single unparseable field must not blank the whole report.
            out[name] = {"at": None, "state": "unparseable", "runtime_seconds": None, "error": None}
    return out
