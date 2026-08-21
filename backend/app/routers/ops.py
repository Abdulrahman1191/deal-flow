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

# A queue is called stalled when work is waiting and NOTHING routed to it has
# completed within this window. Not "no worker answered a ping": both workers
# run --pool=solo, which cannot serve a control broadcast while executing a
# task, and the heavy worker is essentially never idle, so it never answers.
# Built on the ping alone this endpoint reported `heavy` as having no consumer
# while that consumer was visibly draining it.
#
# Real completions are the signal that cannot lie, and they catch a strictly
# larger set of failures: a queue with no consumer AND a worker that is present
# but wedged both show up as "nothing finished lately", where a ping would
# happily report the wedged one as healthy.
QUEUE_STALL_SECONDS = 900.0


class QueueOut(BaseModel):
    name: str
    depth: Optional[int]
    backlog: list[dict]
    sampled: int
    # Workers that ANSWERED a control ping. Informational only: an empty list
    # is not evidence of absence (see QUEUE_STALL_SECONDS above).
    consumers: Optional[list[str]]
    # Work is waiting and nothing routed here has completed lately. Suppressed
    # until the heartbeat store has observed for a full window, so a fresh
    # deploy is never reported as a stall.
    stalled: bool
    seconds_since_completion: Optional[float]


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
    # None until the first task reports. While it is below a task's interval,
    # that task cannot be judged late -- we simply have not watched long enough.
    observing_seconds: Optional[float]
    queues: list[QueueOut]
    unacked: Optional[int]
    tasks: list[TaskOut]


def _age_seconds(iso: Optional[str], now: datetime) -> Optional[float]:
    """Seconds since an ISO timestamp, or None if absent/unparseable."""
    if not iso:
        return None
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return None


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
    observing = task_heartbeat.observing_seconds()

    redis_reachable = any(v.get("depth") is not None for v in raw_queues.values())
    workers_reachable = consumers is not None

    # Age of the most recent completion on each queue, whatever the task and
    # whatever its outcome: a FAILURE or a SKIP still proves something is
    # pulling from that queue, which is the only question being asked here.
    freshest: dict[str, float] = {}
    for task_name, beat in heartbeats.items():
        age = _age_seconds(beat.get("at"), now)
        if age is None:
            continue
        queue = _queue_for_task(task_name)
        if queue not in freshest or age < freshest[queue]:
            freshest[queue] = age

    queues = []
    for name, stats in raw_queues.items():
        subscribed = None if consumers is None else consumers.get(name, [])
        depth = stats.get("depth")
        since_completion = freshest.get(name)

        # Three conditions, all required. Work must be waiting; nothing may have
        # completed within the window; and we must have been watching for at
        # least that long, so a cold start cannot masquerade as a stall.
        stalled = bool(
            depth
            and (since_completion is None or since_completion > QUEUE_STALL_SECONDS)
            and observing is not None
            and observing > QUEUE_STALL_SECONDS
        )

        queues.append(
            QueueOut(
                name=name,
                depth=depth,
                backlog=stats.get("backlog", []),
                sampled=stats.get("sampled", 0),
                consumers=subscribed,
                stalled=stalled,
                seconds_since_completion=since_completion,
            )
        )

    tasks = []
    for schedule_name, entry in (celery.conf.beat_schedule or {}).items():
        task_name = entry.get("task", "")
        seconds = _schedule_seconds(entry.get("schedule"))
        beat = heartbeats.get(task_name) or {}

        last_at = beat.get("at")
        since = _age_seconds(last_at, now)

        stale = None
        if seconds:
            window = seconds * STALE_AFTER_INTERVALS
            if since is not None:
                stale = since > window
            else:
                # Never run. That is a real symptom -- a routing change with no
                # consumer looks exactly like this -- but only once we have
                # watched for longer than the task's own interval. Before that
                # the task simply has not come round yet, and calling it stale
                # turns every deploy into a wall of false alarms.
                stale = observing is not None and observing > window

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
        observing_seconds=observing,
        queues=queues,
        unacked=queue_stats.unacked_count(),
        tasks=tasks,
    )
