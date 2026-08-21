"""
Tests for app/services/queue_stats.py — the broker-side half of
GET /api/v1/ops/queues.

The distinction these pin down is the one that made the 2026-08-20 outage
invisible: depth 0 (healthy, empty) versus depth None (we could not tell).
Collapsing them is how "we don't know" gets reported as "all good".
"""
import base64
import json

from app.services import queue_stats


def _message(task_name: str) -> str:
    """A broker message shaped like Celery protocol 2."""
    return json.dumps({
        "body": base64.b64encode(json.dumps([[], {}, {}]).encode()).decode(),
        "body-encoding": "base64",
        "headers": {"task": task_name, "id": "abc"},
    })


class _FakeRedis:
    def __init__(self, lists=None):
        self.lists = lists or {}
        self.hashes = {}

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:end + 1]

    def hlen(self, key):
        return len(self.hashes.get(key, {}))

    def close(self):
        pass


class _BrokenRedis(_FakeRedis):
    def llen(self, key):
        raise ConnectionError("redis is down")

    def hlen(self, key):
        raise ConnectionError("redis is down")


def _use(monkeypatch, fake):
    monkeypatch.setattr(queue_stats, "client", lambda label: fake)
    return fake


class TestKnownQueues:
    def test_derived_from_celery_config_not_a_hardcoded_list(self):
        names = queue_stats.known_queues()
        # Both real queues must appear, whichever order the routes are declared.
        assert "default" in names
        assert "heavy" in names

    def test_default_queue_is_first(self):
        assert queue_stats.known_queues()[0] == "default"

    def test_legacy_queue_is_included_so_a_regression_is_visible(self):
        assert queue_stats.LEGACY_QUEUE in queue_stats.known_queues()


class TestDepths:
    def test_empty_queue_reports_zero_not_none(self, monkeypatch):
        _use(monkeypatch, _FakeRedis({"default": [], "heavy": []}))

        stats = queue_stats.depths()
        assert stats["default"]["depth"] == 0
        assert stats["heavy"]["depth"] == 0

    def test_unreachable_redis_reports_none_not_zero(self, monkeypatch):
        monkeypatch.setattr(queue_stats, "client", lambda label: None)

        stats = queue_stats.depths()
        assert stats["default"]["depth"] is None
        assert stats["heavy"]["depth"] is None

    def test_erroring_redis_reports_none(self, monkeypatch):
        _use(monkeypatch, _BrokenRedis({"heavy": [_message("t")]}))

        assert queue_stats.depths()["heavy"]["depth"] is None

    def test_backlog_is_broken_down_by_task_name(self, monkeypatch):
        _use(monkeypatch, _FakeRedis({
            "default": [],
            "heavy": (
                [_message("app.tasks.assess_lead.assess_lead_task")] * 3
                + [_message("app.tasks.sync_pitch_decks.sync_pitch_decks_task")] * 2
            ),
        }))

        heavy = queue_stats.depths()["heavy"]
        assert heavy["depth"] == 5
        assert heavy["sampled"] == 5
        assert heavy["backlog"][0] == {
            "task": "app.tasks.assess_lead.assess_lead_task", "count": 3,
        }

    def test_unparseable_messages_are_counted_not_dropped(self, monkeypatch):
        _use(monkeypatch, _FakeRedis({"default": [], "heavy": ["<<garbage>>"]}))

        heavy = queue_stats.depths()["heavy"]
        assert heavy["depth"] == 1
        assert heavy["backlog"] == [{"task": "unparseable", "count": 1}]

    def test_sampling_is_capped_and_reports_how_far_it_got(self, monkeypatch):
        over = queue_stats.BACKLOG_SAMPLE_LIMIT + 25
        _use(monkeypatch, _FakeRedis({
            "default": [],
            "heavy": [_message("app.tasks.assess_lead.assess_lead_task")] * over,
        }))

        heavy = queue_stats.depths()["heavy"]
        # Full depth is still reported; only the breakdown is sampled, and it
        # says so -- a truncated breakdown must never read as a complete one.
        assert heavy["depth"] == over
        assert heavy["sampled"] == queue_stats.BACKLOG_SAMPLE_LIMIT

    def test_empty_legacy_queue_is_omitted_but_a_non_empty_one_is_not(self, monkeypatch):
        _use(monkeypatch, _FakeRedis({"default": [], "heavy": []}))
        assert queue_stats.LEGACY_QUEUE not in queue_stats.depths()

        _use(monkeypatch, _FakeRedis({
            "default": [], "heavy": [], queue_stats.LEGACY_QUEUE: [_message("x")],
        }))
        assert queue_stats.depths()[queue_stats.LEGACY_QUEUE]["depth"] == 1


class TestUnacked:
    def test_counts_in_flight_messages(self, monkeypatch):
        fake = _use(monkeypatch, _FakeRedis())
        fake.hashes["unacked"] = {"a": "1", "b": "2"}

        assert queue_stats.unacked_count() == 2

    def test_unreachable_reports_none(self, monkeypatch):
        monkeypatch.setattr(queue_stats, "client", lambda label: None)
        assert queue_stats.unacked_count() is None


class TestConsumers:
    def test_maps_queues_to_the_workers_subscribed_to_them(self, monkeypatch):
        class _Inspect:
            def active_queues(self):
                return {
                    "platform@host": [{"name": "default"}],
                    "heavy@host": [{"name": "heavy"}],
                }

        monkeypatch.setattr(
            queue_stats.celery.control, "inspect", lambda timeout=None: _Inspect()
        )

        assert queue_stats.consumers_by_queue() == {
            "default": ["platform@host"], "heavy": ["heavy@host"],
        }

    def test_the_2026_08_20_shape_shows_heavy_with_no_consumer(self, monkeypatch):
        """One worker, no -Q, so it consumes `default` only."""
        class _Inspect:
            def active_queues(self):
                return {"platform@host": [{"name": "default"}]}

        monkeypatch.setattr(
            queue_stats.celery.control, "inspect", lambda timeout=None: _Inspect()
        )

        consumers = queue_stats.consumers_by_queue()
        assert consumers.get("heavy") is None
        assert consumers["default"] == ["platform@host"]

    def test_unreachable_workers_report_none_not_empty(self, monkeypatch):
        class _Inspect:
            def active_queues(self):
                return None

        monkeypatch.setattr(
            queue_stats.celery.control, "inspect", lambda timeout=None: _Inspect()
        )

        assert queue_stats.consumers_by_queue() is None

    def test_inspect_raising_reports_none(self, monkeypatch):
        def _boom(timeout=None):
            raise OSError("broker unreachable")

        monkeypatch.setattr(queue_stats.celery.control, "inspect", _boom)
        assert queue_stats.consumers_by_queue() is None
