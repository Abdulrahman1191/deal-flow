from __future__ import annotations
"""
Celery queue depth, read straight from the broker.

The queue names come from the Celery config rather than a hand-kept list, so a
future `task_routes` entry pointing at a new queue shows up here automatically.
That matters: the 2026-08-20 outage WAS a routing change (assess_lead ->
`heavy`) landing without a consumer, and a hardcoded ["default"] here would
have reported perfect health while `heavy` grew to 214 tasks.

`celery` is included when non-empty purely to catch regressions: it is the
legacy list name from before the rename, nothing produces to or consumes from
it, and it had silently accumulated 4,181 dead-lettered tasks before being
cleared on 2026-08-20. Anything landing there again is a bug worth seeing.

Depth is a list length in Redis (LLEN). A task being executed right now is no
longer in the list -- it lives in the broker's `unacked` hash until it is
acknowledged -- so depth counts work that is WAITING, not work in flight.
"""
import base64
import json
from collections import Counter
from typing import Optional

from app.services.redis_util import client
from app.tasks.celery_app import celery

_LABEL = "queue_stats"

# How many messages to parse when breaking a backlog down by task name. Reading
# a queue of a few hundred is cheap; this is a guard against an unbounded LRANGE
# on a runaway queue, and the response reports how many it actually sampled so
# a truncated breakdown can never be mistaken for a complete one.
BACKLOG_SAMPLE_LIMIT = 500

LEGACY_QUEUE = "celery"


def known_queues() -> list[str]:
    """Every queue this app routes to, default first, then routed, then legacy."""
    names = [celery.conf.task_default_queue]
    for route in (celery.conf.task_routes or {}).values():
        queue = route.get("queue") if isinstance(route, dict) else None
        if queue and queue not in names:
            names.append(queue)
    if LEGACY_QUEUE not in names:
        names.append(LEGACY_QUEUE)
    return names


def _task_name_of(raw: str) -> Optional[str]:
    """Task name out of one raw broker message, or None if it can't be read."""
    try:
        message = json.loads(raw)
    except Exception:
        return None
    # Protocol 2 carries the task name in the headers; older/edge messages put
    # it at the top level. Try both rather than assuming.
    headers = message.get("headers") or {}
    return headers.get("task") or message.get("task")


def depths() -> dict:
    """{queue: {"depth": int|None, "backlog": [{task, count}], "sampled": int}}.

    depth is None when Redis could not be reached -- distinct from 0, which is
    a real, healthy, empty queue. Callers must not conflate the two.
    """
    queues = known_queues()
    c = client(_LABEL)
    if c is None:
        return {q: {"depth": None, "backlog": [], "sampled": 0} for q in queues}

    out: dict = {}
    try:
        for q in queues:
            try:
                depth = int(c.llen(q))
            except Exception as exc:
                print(f"[{_LABEL}] LLEN {q} failed: {exc!r}")
                out[q] = {"depth": None, "backlog": [], "sampled": 0}
                continue

            # The legacy queue is only interesting when something is in it.
            if q == LEGACY_QUEUE and depth == 0:
                continue

            backlog: list[dict] = []
            sampled = 0
            if depth:
                try:
                    raws = c.lrange(q, 0, BACKLOG_SAMPLE_LIMIT - 1) or []
                    sampled = len(raws)
                    counts = Counter(_task_name_of(r) or "unparseable" for r in raws)
                    backlog = [
                        {"task": name, "count": n}
                        for name, n in counts.most_common()
                    ]
                except Exception as exc:
                    print(f"[{_LABEL}] LRANGE {q} failed: {exc!r}")
            out[q] = {"depth": depth, "backlog": backlog, "sampled": sampled}
    finally:
        try:
            c.close()
        except Exception:
            pass
    return out


def unacked_count() -> Optional[int]:
    """Messages delivered to a worker but not yet acknowledged (i.e. running).

    None when Redis is unreachable. A steady small number is normal; a number
    that never changes while depth never falls is a wedged worker.
    """
    c = client(_LABEL)
    if c is None:
        return None
    try:
        return int(c.hlen("unacked"))
    except Exception as exc:
        print(f"[{_LABEL}] HLEN unacked failed: {exc!r}")
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def consumers_by_queue() -> Optional[dict]:
    """{queue: [worker names]} for the workers that ANSWERED a control ping.

    INFORMATIONAL ONLY -- never treat an absence here as proof of absence.

    Both workers run --pool=solo, which executes tasks in the main thread, so a
    worker cannot serve control broadcasts while it is running a task. Measured
    on prod 2026-08-21: `deal-flow-worker-heavy` did not answer `inspect
    active_queues` even at a 15-second timeout, because it is essentially never
    idle -- while `platform@` (short tasks, mostly idle) answered instantly. A
    consumer check built on this alone reports a permanent false alarm on the
    one queue that matters most.

    So this is displayed as corroboration and nothing else; whether a queue is
    actually being drained is decided by real task completions -- see
    routers/ops.py, which requires BOTH signals before it claims a stall.

    Returns None when nothing answered at all, which is distinct from {} and
    must not be flattened into "no consumers".
    """
    try:
        active = celery.control.inspect(timeout=1.0).active_queues()
    except Exception as exc:
        print(f"[{_LABEL}] inspect active_queues failed: {exc!r}")
        return None
    if not active:
        return None

    out: dict[str, list[str]] = {}
    for worker, queues in (active or {}).items():
        for q in queues or []:
            name = q.get("name") if isinstance(q, dict) else None
            if name:
                out.setdefault(name, []).append(worker)
    return out
