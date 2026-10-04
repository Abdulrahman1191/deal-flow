"""
Tests for caching deck-verification verdicts (issue #192 item 1).

pitch_deck.verify_match_candidates makes one DeepSeek call per near-miss
candidate, and sync_pitch_decks' Drive-folder sweep reruns every 30 minutes
over every file that hasn't matched yet -- so the same (Drive file, lead)
pair was re-verified forever, always landing on the same verdict (the
2026-09-30 breaker outage traced ~51k failing calls/day largely to this
path). app.services.deck_verification_cache now caches the verdict keyed on
(drive_file_id, lead_id), gated on the deck-text hash and a TTL.

Exercised against a fake SQLAlchemy engine (mirrors _StatefulFakeConn in
test_assess_lead_failure_reason.py) so no live Postgres is needed.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.config import settings
from app.services import claude_agent, deck_verification_cache
from app.services.pitch_deck import MatchCandidate, verify_match_candidates


class _StatefulFakeConn:
    """In-memory stand-in for the deck_verification_cache table: a dict keyed
    on (drive_file_id, lead_id) -> (is_match, deck_text_hash, verified_at),
    dispatched by SQL text the same way deck_verification_cache.get/put issue
    it."""

    def __init__(self):
        self.rows: dict[tuple[str, str], tuple[bool, str, datetime]] = {}

    def execute(self, stmt, params=None):
        sql = str(stmt).strip()
        params = params or {}
        key = (params.get("drive_file_id"), params.get("lead_id"))
        if sql.startswith("SELECT"):
            row = self.rows.get(key)
            return SimpleNamespace(first=lambda: row)
        if sql.startswith("INSERT"):
            self.rows[key] = (params["is_match"], params["deck_text_hash"], datetime.now(timezone.utc))
            return None
        raise AssertionError(f"unexpected SQL in fake deck_verification_cache conn: {sql}")


class _FakeEngine:
    def __init__(self, conn: _StatefulFakeConn):
        self._conn = conn

    def begin(self):
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc_info):
        return False


def _patch_engine(monkeypatch) -> _StatefulFakeConn:
    conn = _StatefulFakeConn()
    monkeypatch.setattr(deck_verification_cache, "create_engine", lambda *a, **k: _FakeEngine(conn))
    return conn


def _candidate(name: str = "Acme") -> MatchCandidate:
    lead = SimpleNamespace(id=uuid.uuid4(), company_name=name, description="", raw_copper_data=None)
    return MatchCandidate(lead=lead, company_name=name, score=0.9)


def _counting_verifier(calls: list, result: bool = True):
    def _verify(*_args, **_kwargs):
        calls.append(1)
        return result
    return _verify


def test_two_sweeps_over_same_unmatched_file_make_one_llm_call(monkeypatch):
    _patch_engine(monkeypatch)
    calls: list = []
    monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _counting_verifier(calls))

    candidate = _candidate()
    first = verify_match_candidates([candidate], "deck text", "drive-file-1")
    second = verify_match_candidates([candidate], "deck text", "drive-file-1")

    assert first is candidate.lead
    assert second is candidate.lead
    assert len(calls) == 1


def test_changed_deck_text_hash_triggers_reverification(monkeypatch):
    _patch_engine(monkeypatch)
    calls: list = []
    monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _counting_verifier(calls))

    candidate = _candidate()
    verify_match_candidates([candidate], "deck text v1", "drive-file-1")
    verify_match_candidates([candidate], "deck text v2 -- content changed", "drive-file-1")

    assert len(calls) == 2


def test_expired_ttl_triggers_reverification(monkeypatch):
    conn = _patch_engine(monkeypatch)
    calls: list = []
    monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _counting_verifier(calls))

    candidate = _candidate()
    verify_match_candidates([candidate], "deck text", "drive-file-1")
    assert len(calls) == 1

    key = ("drive-file-1", str(candidate.lead.id))
    is_match, text_hash, _stale = conn.rows[key]
    conn.rows[key] = (
        is_match, text_hash,
        datetime.now(timezone.utc) - timedelta(days=settings.deck_verification_cache_ttl_days + 1),
    )

    verify_match_candidates([candidate], "deck text", "drive-file-1")
    assert len(calls) == 2


def test_force_bypasses_the_cache(monkeypatch):
    _patch_engine(monkeypatch)
    calls: list = []
    monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _counting_verifier(calls))

    candidate = _candidate()
    verify_match_candidates([candidate], "deck text", "drive-file-1")
    assert len(calls) == 1

    verify_match_candidates([candidate], "deck text", "drive-file-1", force=True)
    assert len(calls) == 2


def test_no_drive_file_id_disables_caching(monkeypatch):
    """Omitting drive_file_id (the existing call shape, still used by tests
    exercising the LLM path directly) must behave exactly as before this
    feature -- every call re-verifies."""
    _patch_engine(monkeypatch)
    calls: list = []
    monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _counting_verifier(calls))

    candidate = _candidate()
    verify_match_candidates([candidate], "deck text")
    verify_match_candidates([candidate], "deck text")

    assert len(calls) == 2


def test_cached_negative_verdict_is_also_reused(monkeypatch):
    _patch_engine(monkeypatch)
    calls: list = []
    monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _counting_verifier(calls, result=False))

    candidate = _candidate()
    first = verify_match_candidates([candidate], "deck text", "drive-file-1")
    second = verify_match_candidates([candidate], "deck text", "drive-file-1")

    assert first is None
    assert second is None
    assert len(calls) == 1
