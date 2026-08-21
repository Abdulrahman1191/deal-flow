"""
Tests for GET /api/v1/ops/queues (app/routers/ops.py).

The endpoint's job is to make the two August incidents legible at a glance, so
the tests are written as those incidents:

  * a queue with work and no consumer  -> no_consumer is True
  * a periodic task that stopped running -> stale is True

Plus the negative cases, which matter just as much: an alarm that fires when
information is merely missing gets ignored, and then the real one is ignored too.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.routers import ops
from app.services.auth import get_current_user

client = TestClient(app)


def _auth_as(email: str = "almuhammed@raed.vc"):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


# Long enough that the endpoint is past its cold-start suppression in tests
# that aren't specifically about cold start.
WARM = 6 * 3600


def _wire(monkeypatch, *, depths=None, consumers=None, heartbeats=None, unacked=0, observing=WARM):
    monkeypatch.setattr(ops.queue_stats, "depths", lambda: depths if depths is not None else {})
    monkeypatch.setattr(ops.queue_stats, "consumers_by_queue", lambda: consumers)
    monkeypatch.setattr(ops.queue_stats, "unacked_count", lambda: unacked)
    monkeypatch.setattr(ops.task_heartbeat, "read_all", lambda: heartbeats or {})
    monkeypatch.setattr(ops.task_heartbeat, "observing_seconds", lambda: observing)


def _get(monkeypatch, **kwargs):
    _wire(monkeypatch, **kwargs)
    _auth_as()
    try:
        response = client.get("/api/v1/ops/queues")
    finally:
        _clear_auth()
    assert response.status_code == 200
    return response.json()


HEALTHY_QUEUES = {
    "default": {"depth": 0, "backlog": [], "sampled": 0},
    "heavy": {"depth": 0, "backlog": [], "sampled": 0},
}
BOTH_CONSUMED = {"default": ["platform@host"], "heavy": ["heavy@host"]}


def test_requires_authentication():
    _clear_auth()
    response = client.get("/api/v1/ops/queues")
    assert response.status_code == 401


def test_is_not_owner_gated(monkeypatch):
    """A non-admin teammate must be able to see pipeline health.

    ADMIN_EMAILS is a two-person list and the endpoint exposes no lead data.
    """
    _wire(monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED)
    _auth_as("waleed@raed.vc")
    try:
        assert client.get("/api/v1/ops/queues").status_code == 200
    finally:
        _clear_auth()


class TestStallDetection:
    """Work waiting + nothing completing. NOT "no worker answered a ping"."""

    def test_a_queue_nothing_is_draining_is_flagged(self, monkeypatch):
        """The 2026-08-20 shape: tasks on `heavy`, no completions on it."""
        body = _get(
            monkeypatch,
            depths={
                "default": {"depth": 0, "backlog": [], "sampled": 0},
                "heavy": {"depth": 214, "backlog": [
                    {"task": "app.tasks.assess_lead.assess_lead_task", "count": 115},
                ], "sampled": 214},
            },
            consumers={"default": ["platform@host"]},
            heartbeats={
                # default keeps working perfectly throughout -- that is what made
                # the outage so hard to see.
                "app.tasks.drain_outbox.drain_copper_outbox_task": {
                    "at": _ago(20), "state": "SUCCESS", "runtime_seconds": 0.4,
                },
            },
        )

        heavy = next(q for q in body["queues"] if q["name"] == "heavy")
        assert heavy["stalled"] is True
        assert heavy["depth"] == 214

        default = next(q for q in body["queues"] if q["name"] == "default")
        assert default["stalled"] is False

    def test_a_busy_solo_worker_that_cannot_answer_a_ping_is_not_a_stall(self, monkeypatch):
        """The false alarm this endpoint shipped with on 2026-08-21.

        `worker-heavy` runs --pool=solo and is never idle, so it never answers
        `inspect active_queues` -- not even at a 15s timeout. It was draining the
        queue the entire time. Silence from a ping must not read as absence.
        """
        body = _get(
            monkeypatch,
            depths={
                "default": {"depth": 0, "backlog": [], "sampled": 0},
                "heavy": {"depth": 202, "backlog": [], "sampled": 202},
            },
            # Only the idle default worker answers.
            consumers={"default": ["platform@host"]},
            heartbeats={
                "app.tasks.sync_pitch_decks.sync_pitch_decks_task": {
                    "at": _ago(45), "state": "SUCCESS", "runtime_seconds": 37.9,
                },
            },
        )

        heavy = next(q for q in body["queues"] if q["name"] == "heavy")
        assert heavy["stalled"] is False
        assert heavy["consumers"] == []
        assert heavy["seconds_since_completion"] is not None

    def test_a_wedged_worker_is_caught_even_though_it_answers_pings(self, monkeypatch):
        """Strictly better than a ping: present but doing nothing still alarms."""
        body = _get(
            monkeypatch,
            depths={
                "default": {"depth": 0, "backlog": [], "sampled": 0},
                "heavy": {"depth": 50, "backlog": [], "sampled": 50},
            },
            consumers={"default": ["platform@host"], "heavy": ["heavy@host"]},
            heartbeats={
                "app.tasks.sync_pitch_decks.sync_pitch_decks_task": {
                    "at": _ago(4 * 3600), "state": "SUCCESS", "runtime_seconds": 37.9,
                },
            },
        )

        heavy = next(q for q in body["queues"] if q["name"] == "heavy")
        assert heavy["stalled"] is True
        assert heavy["consumers"] == ["heavy@host"]

    def test_a_failing_task_still_proves_the_queue_is_being_drained(self, monkeypatch):
        body = _get(
            monkeypatch,
            depths={
                "default": {"depth": 0, "backlog": [], "sampled": 0},
                "heavy": {"depth": 12, "backlog": [], "sampled": 12},
            },
            consumers=None,
            heartbeats={
                "app.tasks.assess_lead.assess_lead_task": {
                    "at": _ago(30), "state": "FAILURE", "runtime_seconds": 2.0,
                    "error": "boom",
                },
            },
        )

        heavy = next(q for q in body["queues"] if q["name"] == "heavy")
        assert heavy["stalled"] is False

    def test_an_empty_queue_is_never_stalled(self, monkeypatch):
        """Nothing waiting means nothing is being missed -- don't cry wolf."""
        body = _get(monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED)

        assert all(q["stalled"] is False for q in body["queues"])

    def test_a_cold_start_is_not_a_stall(self, monkeypatch):
        """A redeploy empties the heartbeat store; a backlog is still waiting."""
        body = _get(
            monkeypatch,
            depths={
                "default": {"depth": 0, "backlog": [], "sampled": 0},
                "heavy": {"depth": 202, "backlog": [], "sampled": 202},
            },
            consumers={"default": ["platform@host"]},
            heartbeats={},
            observing=60,
        )

        heavy = next(q for q in body["queues"] if q["name"] == "heavy")
        assert heavy["stalled"] is False

    def test_never_observed_at_all_is_not_a_stall(self, monkeypatch):
        body = _get(
            monkeypatch,
            depths={
                "default": {"depth": 0, "backlog": [], "sampled": 0},
                "heavy": {"depth": 202, "backlog": [], "sampled": 202},
            },
            consumers=None,
            heartbeats={},
            observing=None,
        )

        heavy = next(q for q in body["queues"] if q["name"] == "heavy")
        assert heavy["stalled"] is False

    def test_healthy_pipeline_flags_nothing(self, monkeypatch):
        body = _get(monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED)

        assert all(q["stalled"] is False for q in body["queues"])
        assert body["workers_reachable"] is True
        assert body["redis_reachable"] is True


class TestRedisReachability:
    def test_depth_none_means_unreachable_not_empty(self, monkeypatch):
        body = _get(
            monkeypatch,
            depths={
                "default": {"depth": None, "backlog": [], "sampled": 0},
                "heavy": {"depth": None, "backlog": [], "sampled": 0},
            },
            consumers=BOTH_CONSUMED,
        )

        assert body["redis_reachable"] is False
        assert all(q["depth"] is None for q in body["queues"])
        assert all(q["stalled"] is False for q in body["queues"])


class TestTaskStaleness:
    def _task(self, body, schedule_name):
        return next(t for t in body["tasks"] if t["schedule_name"] == schedule_name)

    def test_a_task_that_stopped_running_is_stale(self, monkeypatch):
        body = _get(
            monkeypatch,
            depths=HEALTHY_QUEUES,
            consumers=BOTH_CONSUMED,
            heartbeats={
                "app.tasks.sync_pitch_decks.sync_pitch_decks_task": {
                    "at": _ago(5 * 3600), "state": "SUCCESS", "runtime_seconds": 37.9,
                },
            },
        )

        task = self._task(body, "sync-pitch-decks")
        assert task["stale"] is True
        assert task["queue"] == "heavy"

    def test_a_recent_run_is_not_stale(self, monkeypatch):
        body = _get(
            monkeypatch,
            depths=HEALTHY_QUEUES,
            consumers=BOTH_CONSUMED,
            heartbeats={
                "app.tasks.sync_pitch_decks.sync_pitch_decks_task": {
                    "at": _ago(60), "state": "SUCCESS", "runtime_seconds": 37.9,
                },
            },
        )

        assert self._task(body, "sync-pitch-decks")["stale"] is False

    def test_one_missed_interval_is_tolerated(self, monkeypatch):
        """A solo worker legitimately starts a 30-min sweep late."""
        body = _get(
            monkeypatch,
            depths=HEALTHY_QUEUES,
            consumers=BOTH_CONSUMED,
            heartbeats={
                "app.tasks.sync_pitch_decks.sync_pitch_decks_task": {
                    "at": _ago(1800 + 600), "state": "SUCCESS", "runtime_seconds": 40,
                },
            },
        )

        assert self._task(body, "sync-pitch-decks")["stale"] is False

    def test_never_having_run_counts_as_stale_once_we_have_watched_long_enough(self, monkeypatch):
        body = _get(monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED)

        task = self._task(body, "sync-pitch-decks")
        assert task["stale"] is True
        assert task["last_at"] is None

    def test_a_task_that_has_not_come_round_yet_is_not_stale(self, monkeypatch):
        """Four minutes after a deploy, a 30-minute sweep is not "broken".

        The System page's own first deploy flagged six healthy tasks as stale
        this way, which is exactly the false alarm that makes a status page
        worthless.
        """
        body = _get(
            monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED, observing=240,
        )

        assert self._task(body, "sync-pitch-decks")["stale"] is False
        assert self._task(body, "reap-stuck-leads")["stale"] is False

    def test_a_short_interval_task_is_stale_even_soon_after_a_deploy(self, monkeypatch):
        """drain-copper-outbox runs every 30s; 4 minutes of silence is real."""
        body = _get(
            monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED, observing=240,
        )

        assert self._task(body, "drain-copper-outbox")["stale"] is True

    def test_crontab_tasks_report_no_staleness_verdict(self, monkeypatch):
        """"02:00 daily" has no fixed period; a guess would be worse than None."""
        body = _get(monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED)

        task = self._task(body, "dedupe-leads")
        assert task["stale"] is None
        assert task["schedule_seconds"] is None

    def test_failures_carry_their_reason(self, monkeypatch):
        body = _get(
            monkeypatch,
            depths=HEALTHY_QUEUES,
            consumers=BOTH_CONSUMED,
            heartbeats={
                "app.tasks.drain_outbox.drain_copper_outbox_task": {
                    "at": _ago(20), "state": "FAILURE", "runtime_seconds": 1.2,
                    "error": "sslmode is not a valid keyword argument",
                },
            },
        )

        task = self._task(body, "drain-copper-outbox")
        assert task["last_state"] == "FAILURE"
        assert "sslmode" in task["last_error"]

    def test_a_permanently_skipping_task_does_not_look_healthy(self, monkeypatch):
        """A missing env var makes a task return {"skipped": ...} forever."""
        body = _get(
            monkeypatch,
            depths=HEALTHY_QUEUES,
            consumers=BOTH_CONSUMED,
            heartbeats={
                "app.tasks.sync_pitch_decks.sync_pitch_decks_task": {
                    "at": _ago(30), "state": "SKIPPED", "runtime_seconds": 0.01,
                },
            },
        )

        assert self._task(body, "sync-pitch-decks")["last_state"] == "SKIPPED"

    def test_worst_tasks_sort_first(self, monkeypatch):
        body = _get(
            monkeypatch,
            depths=HEALTHY_QUEUES,
            consumers=BOTH_CONSUMED,
            heartbeats={
                "app.tasks.sync_pitch_decks.sync_pitch_decks_task": {
                    "at": _ago(5 * 3600), "state": "SUCCESS", "runtime_seconds": 40,
                },
                "app.tasks.drain_outbox.drain_copper_outbox_task": {
                    "at": _ago(10), "state": "SUCCESS", "runtime_seconds": 0.4,
                },
            },
        )

        assert body["tasks"][0]["stale"] is True

    def test_every_scheduled_task_is_reported(self, monkeypatch):
        body = _get(monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED)

        names = {t["schedule_name"] for t in body["tasks"]}
        assert {"sync-pitch-decks", "sync-copper-leads", "reap-stuck-leads"} <= names

    def test_queue_attribution_matches_celery_routing(self, monkeypatch):
        body = _get(monkeypatch, depths=HEALTHY_QUEUES, consumers=BOTH_CONSUMED)
        by_name = {t["schedule_name"]: t for t in body["tasks"]}

        assert by_name["sync-pitch-decks"]["queue"] == "heavy"
        assert by_name["sync-copper-leads"]["queue"] == "default"
        assert by_name["drain-copper-outbox"]["queue"] == "default"
