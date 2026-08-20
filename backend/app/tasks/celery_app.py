from __future__ import annotations
from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "raedventures",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.assess_lead", "app.tasks.generate_briefing", "app.tasks.sync_copper", "app.tasks.drain_outbox", "app.tasks.dedupe_leads", "app.tasks.sync_pitch_decks", "app.tasks.reap_stuck_leads", "app.tasks.reconcile_ownership"],
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
    },
)
