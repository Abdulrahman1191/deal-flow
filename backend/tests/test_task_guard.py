"""
Tests for the deck-sweep minimum-interval guard (app/services/task_guard.py).

The guard collapses a backlog of duplicate beat-scheduled sweeps. Its two
safety properties are what these tests pin down, because getting either wrong
turns a waste-reducer into an outage:

  * a SKIPPED run must not refresh the timestamp -- otherwise the window slides
    forward on every duplicate and the sweep is starved forever
  * Redis being unreachable must mean PROCEED, never skip
"""
import json
from datetime import datetime, timedelta, timezone

from app.services import task_guard

TASK = "app.tasks.sync_pitch_decks.sync_pitch_decks_task"


class _FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.expires = {}

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def expire(self, key, ttl):
        self.expires[key] = ttl

    def close(self):
        pass


class _BrokenRedis(_FakeRedis):
    def hget(self, key, field):
        raise ConnectionError("redis is down")

    def hset(self, key, field, value):
        raise ConnectionError("redis is down")


def _use(monkeypatch, fake):
    monkeypatch.setattr(task_guard, "client", lambda label: fake)
    return fake


def _stamp(fake, task, *, seconds_ago):
    at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    fake.hashes.setdefault(task_guard.HASH_KEY, {})[task] = json.dumps({"at": at.isoformat()})


class TestShouldSkip:
    def test_never_run_before_proceeds(self, monkeypatch):
        _use(monkeypatch, _FakeRedis())
        assert task_guard.should_skip(TASK, 1500) is None

    def test_recent_run_is_skipped_and_reports_its_age(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        _stamp(fake, TASK, seconds_ago=120)

        since = task_guard.should_skip(TASK, 1500)
        assert since is not None
        assert 100 < since < 200

    def test_run_older_than_the_interval_proceeds(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        _stamp(fake, TASK, seconds_ago=1600)

        assert task_guard.should_skip(TASK, 1500) is None

    def test_force_bypasses_a_recent_run(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        _stamp(fake, TASK, seconds_ago=5)

        assert task_guard.should_skip(TASK, 1500, force=True) is None

    def test_zero_interval_disables_the_guard(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        _stamp(fake, TASK, seconds_ago=5)

        assert task_guard.should_skip(TASK, 0) is None

    def test_another_task_has_its_own_window(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        _stamp(fake, TASK, seconds_ago=5)

        other = "app.tasks.sync_pitch_decks.sync_copper_pitch_deck_links_task"
        assert task_guard.should_skip(other, 1500) is None


class TestFailsOpen:
    def test_redis_unreachable_proceeds(self, monkeypatch):
        monkeypatch.setattr(task_guard, "client", lambda label: None)
        assert task_guard.should_skip(TASK, 1500) is None

    def test_redis_erroring_proceeds(self, monkeypatch):
        _use(monkeypatch, _BrokenRedis())
        assert task_guard.should_skip(TASK, 1500) is None

    def test_mark_ran_never_raises_when_redis_is_down(self, monkeypatch):
        _use(monkeypatch, _BrokenRedis())
        task_guard.mark_ran(TASK)  # must not raise

    def test_corrupt_timestamp_proceeds(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        fake.hashes.setdefault(task_guard.HASH_KEY, {})[TASK] = "not json"

        assert task_guard.should_skip(TASK, 1500) is None


class TestSkippingDoesNotSlideTheWindow:
    """The starvation bug this guard must never have.

    If a skipped duplicate refreshed the timestamp, each of the 59 queued
    duplicates would push the window forward and the sweep would stop running
    altogether. Only mark_ran -- called solely on the proceed path -- writes.
    """

    def test_repeated_skips_leave_the_original_timestamp(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        _stamp(fake, TASK, seconds_ago=1400)
        before = fake.hashes[task_guard.HASH_KEY][TASK]

        for _ in range(20):
            assert task_guard.should_skip(TASK, 1500) is not None

        assert fake.hashes[task_guard.HASH_KEY][TASK] == before

    def test_backlog_drains_then_the_next_scheduled_run_proceeds(self, monkeypatch):
        """One real run, N instant skips, and the sweep still runs next interval."""
        fake = _use(monkeypatch, _FakeRedis())

        assert task_guard.should_skip(TASK, 1500) is None
        task_guard.mark_ran(TASK)

        for _ in range(58):
            assert task_guard.should_skip(TASK, 1500) is not None

        # The next beat tick, half an hour later.
        _stamp(fake, TASK, seconds_ago=1800)
        assert task_guard.should_skip(TASK, 1500) is None
