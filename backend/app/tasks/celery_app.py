from __future__ import annotations
from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "raedventures",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.assess_lead", "app.tasks.generate_briefing", "app.tasks.sync_copper", "app.tasks.drain_outbox", "app.tasks.dedupe_leads", "app.tasks.sync_pitch_decks", "app.tasks.reap_stuck_leads", "app.tasks.reconcile_ownership", "app.tasks.redrive_outbox"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="default",
    # Queue isolation (issue #129): a hung/OOMing assessment must never be able
    # to wedge Copper sync/write-backs by taking the worker process down with it.
    # `heavy` carries the crash-prone AI work (assessment + deck ingestion/OCR);
    # `default` carries lightweight CRM plumbing that has to stay responsive
    # regardless of what's happening on `heavy`.
    #
    # DEPLOYMENT: this repo/agent can't edit the Dockerfile or process manager,
    # so this only takes effect once whoever manages process config runs TWO
    # worker processes instead of one:
    #   celery -A app.tasks.celery_app worker -Q heavy   --concurrency=1  (higher memory limit)
    #   celery -A app.tasks.celery_app worker -Q default --concurrency=<as today>
    # Until both are running, a single worker consuming both queues still
    # works (Celery just interleaves them) but doesn't get the isolation.
    task_routes={
        "app.tasks.assess_lead.*": {"queue": "heavy"},
        "app.tasks.sync_pitch_decks.*": {"queue": "heavy"},  # Drive/OCR deck ingestion
        "app.tasks.sync_copper.*": {"queue": "default"},
        "app.tasks.drain_outbox.*": {"queue": "default"},
        "app.tasks.reap_stuck_leads.*": {"queue": "default"},
        "app.tasks.reconcile_ownership.*": {"queue": "default"},
        "app.tasks.dedupe_leads.*": {"queue": "default"},
        "app.tasks.generate_briefing.*": {"queue": "default"},
    },
    beat_schedule={
        "daily-briefing": {
            "task": "app.tasks.generate_briefing.generate_all_briefings_task",
            "schedule": crontab(hour=settings.briefing_cron_hour, minute=settings.briefing_cron_minute),
        },
        # Reconcile the board with Copper every 5 minutes: import leads newly
        # assigned to the user, archive ones reassigned away/closed. Gated at
        # runtime by the DISABLE_COPPER_SYNC env var (set it true to pause).
        "sync-copper-leads": {
            "task": "app.tasks.sync_copper.sync_copper_leads_task",
            "schedule": 300.0,  # every 5 minutes
        },
        "drain-copper-outbox": {
            "task": "app.tasks.drain_outbox.drain_copper_outbox_task",
            "schedule": 30.0,  # every 30 seconds
        },
        # Firm-wide, status-agnostic ownership reconcile against Copper's
        # current assignee_id (issue #123). Closes the gap left by
        # sync-copper-leads above, which only reassigns leads Copper reports
        # as open-status-assigned to an actively-synced user.
        "reconcile-ownership": {
            "task": "app.tasks.reconcile_ownership.reconcile_ownership_task",
            "schedule": 900.0,  # every 15 minutes
        },
        # Collapse duplicate-name leads automatically (archives extras, reversible).
        # Runs daily at 02:00 UTC; also safe to run the CLI (scripts/dedupe_leads.py)
        # ad-hoc. Idempotent, so the daily run is a no-op when there's nothing to do.
        "dedupe-leads": {
            "task": "app.tasks.dedupe_leads.dedupe_leads_task",
            "schedule": crontab(hour=2, minute=0),
        },
        # Scan the pitch-deck Drive folder for new files and attach them to
        # leads (see app/tasks/sync_pitch_decks.py). No-ops until a maintainer
        # sets GOOGLE_SERVICE_ACCOUNT_JSON.
        "sync-pitch-decks": {
            "task": "app.tasks.sync_pitch_decks.sync_pitch_decks_task",
            "schedule": 1800.0,  # every 30 minutes
        },
        # Sibling sweep: attach decks linked via Copper's own "Pitch Deck" URL
        # custom field (see app/tasks/sync_pitch_decks.py). No-ops until a
        # maintainer sets GOOGLE_SERVICE_ACCOUNT_JSON + COPPER_CF_PITCH_DECK_URL_ID.
        "sync-copper-pitch-deck-links": {
            "task": "app.tasks.sync_pitch_decks.sync_copper_pitch_deck_links_task",
            "schedule": 1800.0,  # every 30 minutes, same cadence as the Drive-folder sweep
        },
        # Self-healing backstop for leads orphaned by a worker crash mid-assessment
        # (issue #100): re-enqueues any 'processing'/'pending' lead whose updated_at
        # is older than settings.assessment_reap_after_minutes. See
        # app/tasks/reap_stuck_leads.py for why this is safe to run repeatedly.
        "reap-stuck-leads": {
            "task": "app.tasks.reap_stuck_leads.reap_stuck_leads_task",
            "schedule": 600.0,  # every 10 minutes
        },
        # Self-healing backstop for copper_outbox rows that exhausted
        # drain_copper_outbox_task's 5 delivery attempts (issue #131): resets
        # 'failed' rows back to 'pending' so a transient Copper outage doesn't
        # permanently strand a write-back. Bounded by
        # settings.outbox_max_redrives per row -- see app/tasks/redrive_outbox.py.
        "redrive-failed-copper-outbox": {
            "task": "app.tasks.redrive_outbox.redrive_failed_outbox_task",
            "schedule": settings.outbox_redrive_interval_seconds,
        },
    },
)


# ---------------------------------------------------------------------------
# Task heartbeats (GET /api/v1/ops/queues)
#
# Recorded via signals rather than in each task body so coverage cannot rot: a
# task added later is reported without its author remembering to. The handlers
# are deliberately trivial and swallow everything -- observability must never
# be able to fail the work it is observing.
#
# Timing is kept per task id, not in a single variable, because the `default`
# worker interleaves several short tasks and a bare start-time would be
# overwritten between prerun and postrun.
# ---------------------------------------------------------------------------
from celery.signals import task_postrun, task_prerun  # noqa: E402

_started_at: dict = {}


@task_prerun.connect
def _record_task_start(task_id=None, task=None, **_kwargs):
    import time

    try:
        _started_at[task_id] = time.monotonic()
    except Exception:
        pass


@task_postrun.connect
def _record_task_end(task_id=None, task=None, state=None, retval=None, **_kwargs):
    import time

    try:
        from app.services import task_heartbeat

        started = _started_at.pop(task_id, None)
        runtime = (time.monotonic() - started) if started is not None else None

        # A task that returned {"skipped": ...} ran fine but did no work. Saying
        # "success" there would let a permanently-skipping task (a missing env
        # var, say) look perfectly healthy forever, which is precisely the class
        # of silent failure this endpoint exists to expose.
        reported = state
        if state == "SUCCESS" and isinstance(retval, dict) and retval.get("skipped"):
            reported = "SKIPPED"

        # On FAILURE, Celery passes the exception itself as retval -- keeping a
        # trimmed string of it means the status line says WHY, not just "broken".
        error = str(retval) if state == "FAILURE" and retval is not None else None

        task_heartbeat.record(
            getattr(task, "name", "") or "",
            state=reported or "UNKNOWN",
            runtime_seconds=runtime,
            error=error,
        )
    except Exception:
        pass
