"""Tests for the deck-extraction cache (app/services/deck_cache.py).

The cache exists to stop sync_pitch_decks re-downloading and re-extracting the
same unchanged Drive PDF on every 30-minute sweep.
"""
from app.services import deck_cache


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.closed = False

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.store[k] = v

    def delete(self, k):
        self.store.pop(k, None)

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]

    def close(self):
        self.closed = True


class _BrokenRedis(_FakeRedis):
    def get(self, k):
        raise ConnectionError("redis is down")

    def setex(self, k, ttl, v):
        raise ConnectionError("redis is down")


class TestRoundTrip:
    def test_miss_returns_none_then_hit_returns_value(self, monkeypatch):
        monkeypatch.setattr(deck_cache, "_client", lambda: _FakeRedis())
        # a shared store so put/get see each other
        shared = _FakeRedis()
        monkeypatch.setattr(deck_cache, "_client", lambda: shared)

        assert deck_cache.get("file1", "t1") is None
        deck_cache.put("file1", "deck text", "t1")
        assert deck_cache.get("file1", "t1") == "deck text"

    def test_empty_extraction_is_cached_as_a_hit_not_a_miss(self, monkeypatch):
        """The whole point: a deck that yields nothing must not be re-extracted
        every sweep. "" is a hit, None is a miss -- they must stay distinct."""
        shared = _FakeRedis()
        monkeypatch.setattr(deck_cache, "_client", lambda: shared)

        deck_cache.put("file1", "", "t1")
        assert deck_cache.get("file1", "t1") == ""
        assert deck_cache.get("file1", "t1") is not None

    def test_modified_time_change_is_a_miss(self, monkeypatch):
        """An edited deck must be re-read, not served from the old version."""
        shared = _FakeRedis()
        monkeypatch.setattr(deck_cache, "_client", lambda: shared)

        deck_cache.put("file1", "old text", "t1")
        assert deck_cache.get("file1", "t2") is None

    def test_invalidate_without_modified_time_clears_every_version(self, monkeypatch):
        shared = _FakeRedis()
        monkeypatch.setattr(deck_cache, "_client", lambda: shared)

        deck_cache.put("file1", "a", "t1")
        deck_cache.put("file1", "b", "t2")
        deck_cache.invalidate("file1")

        assert deck_cache.get("file1", "t1") is None
        assert deck_cache.get("file1", "t2") is None


class TestDegradesGracefully:
    def test_no_redis_client_is_a_miss_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(deck_cache, "_client", lambda: None)
        assert deck_cache.get("file1", "t1") is None
        deck_cache.put("file1", "x", "t1")      # must not raise
        deck_cache.invalidate("file1")          # must not raise

    def test_redis_errors_degrade_to_a_miss(self, monkeypatch):
        monkeypatch.setattr(deck_cache, "_client", lambda: _BrokenRedis())
        assert deck_cache.get("file1", "t1") is None
        deck_cache.put("file1", "x", "t1")      # must not raise

    def test_client_is_closed_after_use(self, monkeypatch):
        client = _FakeRedis()
        monkeypatch.setattr(deck_cache, "_client", lambda: client)
        deck_cache.get("file1", "t1")
        assert client.closed
