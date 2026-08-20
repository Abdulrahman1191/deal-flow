"""
Tests for the worker-crash-loop bound (issue #129).

acks_late=True + task_reject_on_worker_lost=True means a task that reliably
crashes the worker (OOM/hard-kill) is redelivered forever -- worker-lost
redeliveries bypass Celery's own max_retries entirely. assess_lead_task now
tracks a per-lead assessment_attempts counter (bumped at the very start of
every attempt, including redeliveries) and dead-letters the lead to 'failed'
once it exceeds MAX_ASSESS_ATTEMPTS instead of running again -- and resets
the counter to 0 on any clean outcome so a lead that eventually succeeds
isn't permanently poisoned by earlier transient failures.

Exercises assess_lead_task directly (a bound Celery task can be called like a
plain function outside the broker) with _increment_attempts/_mark_failed/_run
monkeypatched, so no live Postgres or broker is needed.
"""
from __future__ import annotations
import uuid

from celery.exceptions import SoftTimeLimitExceeded

from app.tasks import assess_lead


def test_attempt_cap_exceeded_dead_letters_without_running(monkeypatch):
    lead_id = str(uuid.uuid4())
    monkeypatch.setattr(
        assess_lead, "_increment_attempts", lambda lid: assess_lead.MAX_ASSESS_ATTEMPTS + 1
    )

    def _boom(_lid):
        raise AssertionError("must not run the assessment once the attempt cap is exceeded")

    monkeypatch.setattr(assess_lead, "_run", _boom)

    failed = []
    monkeypatch.setattr(assess_lead, "_mark_failed", lambda lid, error: failed.append((lid, error)))

    result = assess_lead.assess_lead_task(lead_id)

    assert result["status"] == "failed"
    assert failed == [(lead_id, f"exceeded {assess_lead.MAX_ASSESS_ATTEMPTS} assessment attempts (attempt #4)")]


def test_attempt_within_cap_runs_normally(monkeypatch):
    lead_id = str(uuid.uuid4())
    monkeypatch.setattr(assess_lead, "_increment_attempts", lambda lid: 1)

    async def _fake_run(lid):
        return {"lead_id": lid, "status": "assessed"}

    monkeypatch.setattr(assess_lead, "_run", _fake_run)
    monkeypatch.setattr(
        assess_lead, "_mark_failed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fail"))
    )

    result = assess_lead.assess_lead_task(lead_id)

    assert result == {"lead_id": lead_id, "status": "assessed"}


def test_soft_time_limit_exceeded_marks_failed_instead_of_crashing(monkeypatch):
    """A hung DeepSeek/Tavily call raises SoftTimeLimitExceeded inside the
    task -- it must be caught and turned into a clean 'failed' lead, not
    propagate and force a hard SIGKILL of the worker."""
    lead_id = str(uuid.uuid4())
    monkeypatch.setattr(assess_lead, "_increment_attempts", lambda lid: 1)

    async def _fake_run(lid):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(assess_lead, "_run", _fake_run)

    failed = []
    monkeypatch.setattr(assess_lead, "_mark_failed", lambda lid, error: failed.append((lid, error)))

    result = assess_lead.assess_lead_task(lead_id)

    assert result["status"] == "failed"
    assert failed and failed[0][0] == lead_id
