from __future__ import annotations
"""
Operational status of the background pipeline — GET /api/v1/ops/queues.

The gap this closes: deal-flow had two production incidents in August where
every liveness signal stayed green while the pipeline was dead.

  * 2026-08-18 — the Tesseract OCR tier pegged the worker at 234% CPU. The
    container was "Up", and because the image never set PYTHONUNBUFFERED,
    `docker logs` was empty for 12 hours. It read as a crashed worker; it was
    a busy one.
  * 2026-08-20 — assess_lead/sync_pitch_decks were routed to `heavy` while the
    only worker consumed `default`. Assessments stopped at 11:05. RestartCount
    0, CPU 0.16%, /health {"status":"ok","db":"ok"}, Copper sync working.

Both were diagnosed by hand over SSH with `redis-cli LLEN`. Neither was
visible in the product. This endpoint puts the three facts that actually
distinguish a working pipeline from a dead one in one place:

  1. queue depth        — how much work is waiting
  2. queue consumers    — whether anything is subscribed to drain it
  3. per-task last run  — whether the thing that drains it has run lately

Any one of them alone is ambiguous. Depth 0 means "healthy" or "nobody is
producing"; a live worker means "consuming something", not necessarily this
queue; a recent run means "it ran", not "it kept up". Together they aren't.

AUTH: signed-in users, NOT owner-gated. It exposes no lead, applicant or
assessment data -- only queue names, counts and task timestamps. The whole
point is that an incident should be visible without SSH access to the prod
host, and ADMIN_EMAILS is a two-person list. Owner-gate it if that changes.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.models.user import User
from app.services import queue_stats, task_heartbeat
from app.services.auth import get_current_user
from app.tasks.celery_app import celery

router = APIRouter(prefix="/ops", tags=["ops"])

# A periodic task is called stale once it has missed roughly two turns. One
# missed turn is normal here: the workers run --pool=solo, so a 30-minute sweep
# that happens to sit behind a slow predecessor legitimately starts late. Two
# is not explainable that way.
STALE_AFTER_INTERVALS = 2.5


class QueueOut(BaseModel):
    name: str
    depth: Optional[int]
    backlog: list[dict]
    sampled: int
    consumers: Optional[list[str]]
    # True only when we positively know there is work and nothing consuming it.
    # Unknown consumers (workers unreachable) must not raise this -- an alarm
    # that fires on missing information trains people to ignore it.
    no_consumer: bool


class TaskOut(BaseModel):
    schedule_name: str
    task: str
    queue: str
    schedule_seconds: Optional[float]
    schedule: str
    last_state: Optional[str]
    last_at: Optional[str]
    last_runtime_seconds: Optional[float]
    last_error: Optional[str]
    seconds_since: Optional[float]
    stale: Optional[bool]


class OpsOut(BaseModel):
    generated_at: str
    redis_reachable: bool
    workers_reachable: bool
    queues: list[QueueOut]
    unacked: Optional[int]
    tasks: list[TaskOut]


def _queue_for_task(task_name: str) -> str:
    """Resolve a task's queue the same way Celery's router does."""
    for pattern, route in (celery.conf.task_routes or {}).items():
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        if task_name.startswith(prefix):
            queue = route.get("queue") if isinstance(route, dict) else None
            if queue:
                return queue
    return celery.conf.task_default_queue


def _schedule_seconds(schedule) -> Optional[float]:
    """Seconds for an interval schedule; None for a crontab.

    Celery's crontab has no meaningful fixed period (it is "02:00 daily", not
    "every N seconds"), so staleness is left unevaluated for those rather than
    guessed at. A wrong staleness verdict is worse than none.
    """
    if isinstance(schedule, (int, float)):
        return float(schedule)
    seconds = getattr(schedule, "seconds", None)
    return float(seconds) if isinstance(seconds, (int, float)) else None


@router.get("/queues", response_model=OpsOut)
async def queue_status(user: User = Depends(get_current_user)) -> OpsOut:
    now = datetime.now(timezone.utc)

    raw_queues = queue_stats.depths()
    consumers = queue_stats.consumers_by_queue()
    heartbeats = task_heartbeat.read_all()

    redis_reachable = any(v.get("depth") is not None for v in raw_queues.values())
    workers_reachable = consumers is not None

    queues = []
    for name, stats in raw_queues.items():
        subscribed = None if consumers is None else consumers.get(name, [])
        depth = stats.get("depth")
        queues.append(
            QueueOut(
                name=name,
                depth=depth,
                backlog=stats.get("backlog", []),
                sampled=stats.get("sampled", 0),
                consumers=subscribed,
                no_consumer=bool(subscribed is not None and not subscribed and depth),
            )
        )

    tasks = []
    for schedule_name, entry in (celery.conf.beat_schedule or {}).items():
        task_name = entry.get("task", "")
        seconds = _schedule_seconds(entry.get("schedule"))
        beat = heartbeats.get(task_name) or {}

        last_at = beat.get("at")
        since = None
        if last_at:
            try:
                since = (now - datetime.fromisoformat(last_at)).total_seconds()
            except Exception:
                since = None

        stale = None
        if seconds:
            # Never run at all is stale too -- that is exactly what a routing
            # change with no consumer looks like on a fresh worker.
            stale = True if since is None else since > seconds * STALE_AFTER_INTERVALS

        tasks.append(
            TaskOut(
                schedule_name=schedule_name,
                task=task_name,
                queue=_queue_for_task(task_name),
                schedule_seconds=seconds,
                schedule=f"every {seconds:g}s" if seconds else str(entry.get("schedule")),
                last_state=beat.get("state"),
                last_at=last_at,
                last_runtime_seconds=beat.get("runtime_seconds"),
                last_error=beat.get("error"),
                seconds_since=since,
                stale=stale,
            )
        )

    # Worst first: stale before healthy, then longest-silent first, so the
    # answer to "is anything wrong" is the top row rather than a scan.
    tasks.sort(key=lambda t: (not t.stale, -(t.seconds_since or 0)))

    return OpsOut(
        generated_at=now.isoformat(),
        redis_reachable=redis_reachable,
        workers_reachable=workers_reachable,
        queues=queues,
        unacked=queue_stats.unacked_count(),
        tasks=tasks,
    )
