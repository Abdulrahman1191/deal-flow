from __future__ import annotations
"""
Minimum-interval guard for beat-scheduled sweeps.

The problem it solves, measured on prod 2026-08-21: the `heavy` queue held 214
tasks, 59 of them sync_pitch_decks and 40 sync_copper_pitch_deck_links. At one
run per 30 minutes that is ~29 hours of accumulation. Beat enqueues each sweep
on its interval whether or not the previous one is still waiting, and the
workers run --pool=solo, so whenever a sweep takes longer than its interval the
queue grows without bound and never recovers on its own. Tesseract made each
sweep slow enough to start that; the backlog outlived the fix.

Running the same folder sweep 59 times in a row is pure waste -- each pass
re-lists the same Drive folder and re-verifies the same unmatched filenames
through the LLM. This guard makes the duplicates return immediately, so a
backlog drains in seconds instead of hours and steady state is one run per
interval.

Why a timestamp and not a lock: a lock only prevents CONCURRENT runs, and with
--pool=solo there is never any concurrency to prevent -- the duplicates run
back-to-back, which is exactly the waste. What matters is how long ago the work
was last actually done.

Two rules make this safe:

  * Only a run that actually PROCEEDS records a timestamp. If skips refreshed
    it, the window would slide forward on every skipped duplicate and the sweep
    could be starved indefinitely -- a guard that silently stops the thing it
    guards is worse than the backlog.
  * Redis unreachable means DON'T skip. Failing open costs a duplicate sweep;
    failing closed would silently stop deck ingestion, which is the exact class
    of invisible outage this whole change exists to prevent.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from app.services.redis_util import client

HASH_KEY = "task_last_full_run:v1"
TTL_SECONDS = 7 * 24 * 3600
_LABEL = "task_guard"


def seconds_since_last_run(task_name: str) -> Optional[float]:
    """Seconds since this task last actually ran, or None if unknown.

    None covers both "never recorded" and "Redis unreachable" because callers
    must treat them identically: proceed.
    """
    c = client(_LABEL)
    if c is None:
        return None
    try:
        raw = c.hget(HASH_KEY, task_name)
        if not raw:
            return None
        at = datetime.fromisoformat(json.loads(raw)["at"])
        return (datetime.now(timezone.utc) - at).total_seconds()
    except Exception as exc:
        print(f"[{_LABEL}] could not read last run for {task_name}: {exc!r}")
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def mark_ran(task_name: str) -> None:
    """Record that this task is doing its work NOW. Call only when proceeding."""
    c = client(_LABEL)
    if c is None:
        return
    try:
        c.hset(HASH_KEY, task_name, json.dumps({"at": datetime.now(timezone.utc).isoformat()}))
        c.expire(HASH_KEY, TTL_SECONDS)
    except Exception as exc:
        print(f"[{_LABEL}] could not record run for {task_name}: {exc!r}")
    finally:
        try:
            c.close()
        except Exception:
            pass


def should_skip(task_name: str, min_interval_seconds: float, *, force: bool = False) -> Optional[float]:
    """Seconds since the last run if this call should be skipped, else None.

    `force=True` always proceeds -- an operator asking for a sweep by hand is
    never answered with "not yet".
    """
    if force or min_interval_seconds <= 0:
        return None
    since = seconds_since_last_run(task_name)
    if since is not None and since < min_interval_seconds:
        return since
    return None
