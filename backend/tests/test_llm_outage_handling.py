"""
DeepSeek account-level outages (402 no balance / 401 bad key) must pause, not hammer.

Production, 2026-09-30 16:15Z onward: the DeepSeek balance ran out. Every
30-minute Drive sweep re-verified each unmatched deck candidate by candidate
(~35 failing calls a minute, ~51k a day), and assess_lead burned attempts and
redrives on leads with nothing wrong with them. These tests drive the real
breaker end to end: a fake DeepSeek client raising the exact 402 production got
(message copied from deal-flow-worker-heavy logs), and an in-memory Redis.
"""
from __future__ import annotations
import asyncio
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.services import claude_agent, llm_breaker
from app.services.pitch_deck import MatchCandidate, MatchResult, verify_match_candidates
from app.tasks import assess_lead
from app.tasks import redrive_failed_assessments as redrive
from app.tasks import sync_pitch_decks as spd

RECORDED_402_BODY = {"error": {
    "message": "Insufficient Balance (request_id: bcbc38a3-583f-4de8-897a-93313beb3e17)",
    "type": "unknown_error", "param": None, "code": "invalid_request_error"}}


def _status_error(code, body=RECORDED_402_BODY):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(code, request=request, json=body)
    return APIStatusError(f"Error code: {code} - {body}", response=response, body=body)


class _FakeRedis:
    def __init__(self):
        self.store, self.ttls = {}, {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value.encode()
        self.ttls[key] = ex


class _FakeDeepSeek:
    """Counts calls; raises `error` for every call (or returns `reply`)."""

    def __init__(self, error=None, reply='{"is_match": true}'):
        self.error, self.reply, self.calls = error, reply, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(llm_breaker, "_redis", r)
    monkeypatch.setattr(llm_breaker, "_local_open_until", 0.0)
    monkeypatch.setattr(llm_breaker.settings, "llm_outage_pause_seconds", 900)
    return r


def _use_deepseek(monkeypatch, fake):
    monkeypatch.setattr(claude_agent, "_get_client", lambda: fake)


# --- the wrapper every DeepSeek call goes through --------------------------------

def test_402_opens_the_shared_breaker_and_later_calls_never_reach_deepseek(monkeypatch, fake_redis):
    fake = _FakeDeepSeek(error=_status_error(402))
    _use_deepseek(monkeypatch, fake)

    with pytest.raises(llm_breaker.LLMUnavailable) as first:
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")
    assert "402" in str(first.value) and "Insufficient Balance" in str(first.value)
    assert fake_redis.ttls[llm_breaker._KEY] == 900

    for _ in range(5):
        with pytest.raises(llm_breaker.LLMUnavailable):
            claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")
    assert fake.calls == 1


def test_401_bad_key_is_account_level_too(monkeypatch, fake_redis):
    _use_deepseek(monkeypatch, _FakeDeepSeek(error=_status_error(401, {"error": {"message": "Authentication Fails"}})))
    with pytest.raises(llm_breaker.LLMUnavailable):
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")
    assert llm_breaker.open_reason() is not None


def test_ordinary_errors_do_not_open_the_breaker(monkeypatch, fake_redis):
    fake = _FakeDeepSeek(error=_status_error(500, {"error": {"message": "server error"}}))
    _use_deepseek(monkeypatch, fake)
    with pytest.raises(APIStatusError):
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")
    assert llm_breaker.open_reason() is None
    with pytest.raises(APIStatusError):
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")
    assert fake.calls == 2  # a 500 is per-call, still retried normally


def test_breaker_expiry_lets_one_probe_through(monkeypatch, fake_redis):
    fake = _FakeDeepSeek(error=_status_error(402))
    _use_deepseek(monkeypatch, fake)
    with pytest.raises(llm_breaker.LLMUnavailable):
        claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text")
    fake_redis.store.clear()  # TTL elapsed
    fake.error = None  # balance topped up
    assert claude_agent.verify_pitch_deck_match("Acme", "desc", "deck text") is True
    assert fake.calls == 2


def test_breaker_still_protects_this_process_when_redis_is_down(monkeypatch):
    class _DeadRedis:
        def get(self, key):
            raise ConnectionError("redis down")

        def set(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(llm_breaker, "_redis", _DeadRedis())
    monkeypatch.setattr(llm_breaker, "_local_open_until", 0.0)
    assert llm_breaker.open_reason() is None
    llm_breaker.open_breaker("DeepSeek 402")
    assert llm_breaker.open_reason() is not None
    monkeypatch.setattr(llm_breaker, "_local_open_until", time.monotonic() - 1)
    assert llm_breaker.open_reason() is None


# --- deck verification ------------------------------------------------------------

def _candidate(name):
    return MatchCandidate(lead=SimpleNamespace(id=uuid.uuid4(), company_name=name, description="d",
                                               raw_copper_data=None), company_name=name, score=0.8)


def test_verify_match_candidates_stops_at_the_first_account_level_failure(monkeypatch, fake_redis):
    fake = _FakeDeepSeek(error=_status_error(402))
    _use_deepseek(monkeypatch, fake)
    with pytest.raises(llm_breaker.LLMUnavailable):
        verify_match_candidates([_candidate("NEOM"), _candidate("One Horizon"), _candidate("Alweb.ai")], "deck")
    assert fake.calls == 1


class _FakeLeadsResult:
    def __init__(self, leads):
        self._leads = leads

    def scalars(self):
        return self

    def all(self):
        return self._leads


class _FakeRunSession:
    def __init__(self, leads):
        self._leads = leads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeLeadsResult(self._leads)

    async def commit(self):
        pass


def _wire_sweep(monkeypatch, files, candidates_per_file=2):
    leads = [SimpleNamespace(id=uuid.uuid4(), company_name=f"Co {i}", status="pending", pitch_deck_drive_id=None,
                             pitch_deck_text=None, description="d", raw_copper_data=None) for i in range(3)]
    downloads = []
    monkeypatch.setattr(spd, "_drive_service", lambda: None)
    monkeypatch.setattr(spd, "_list_pdfs_in_folder",
                        lambda service, folder_id: [{"id": f"f{i}", "name": f"Deck {i}.pdf"} for i in range(files)])
    monkeypatch.setattr(spd, "CelerySessionLocal", lambda: _FakeRunSession(leads))
    monkeypatch.setattr(spd.settings, "deck_match_verify_enabled", True)
    monkeypatch.setattr(spd, "find_lead_match", lambda name, remaining, sender_domain=None: MatchResult(
        lead=None, candidates=[], needs_verification=[_candidate(f"{name} cand {j}") for j in range(candidates_per_file)]))
    monkeypatch.setattr(spd, "_download_and_extract", lambda service, f: downloads.append(f["name"]) or "deck text")
    return downloads


def test_drive_sweep_stops_verifying_after_the_first_402(monkeypatch, fake_redis):
    fake = _FakeDeepSeek(error=_status_error(402))
    _use_deepseek(monkeypatch, fake)
    downloads = _wire_sweep(monkeypatch, files=40)

    result = asyncio.run(spd._run())

    assert fake.calls == 1          # was 40 files x 2 candidates = 80 failing calls
    assert downloads == ["Deck 0.pdf"]  # no downloads done only for verification
    assert result["unmatched"] == 40 and result["failed"] == 0


def test_drive_sweep_skips_verification_entirely_while_the_breaker_is_open(monkeypatch, fake_redis):
    llm_breaker.open_breaker("DeepSeek 402: Insufficient Balance")
    fake = _FakeDeepSeek()
    _use_deepseek(monkeypatch, fake)
    downloads = _wire_sweep(monkeypatch, files=10)

    result = asyncio.run(spd._run())

    assert fake.calls == 0 and downloads == []
    assert result["unmatched"] == 10


# --- assess_lead / redrive --------------------------------------------------------

def test_assess_lead_parks_without_spending_an_attempt_while_paused(monkeypatch, fake_redis):
    llm_breaker.open_breaker("DeepSeek 402: Insufficient Balance")
    must_not = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called while paused"))
    monkeypatch.setattr(assess_lead, "_increment_attempts", must_not)
    monkeypatch.setattr(assess_lead, "_run", must_not)
    monkeypatch.setattr(assess_lead, "_mark_failed", must_not)

    lid = str(uuid.uuid4())
    result = assess_lead.assess_lead_task(lid)

    assert result["status"] == "parked_llm_unavailable" and "402" in result["reason"]


def test_assess_lead_parks_instead_of_failing_or_retrying_on_a_mid_run_402(monkeypatch, fake_redis):
    monkeypatch.setattr(assess_lead, "_increment_attempts", lambda lid: 1)

    async def _run(lid):
        raise llm_breaker.LLMUnavailable("DeepSeek 402: Insufficient Balance")
    monkeypatch.setattr(assess_lead, "_run", _run)
    parked = []
    monkeypatch.setattr(assess_lead, "_park_for_llm_outage", lambda lid, reason: parked.append((lid, reason)))
    monkeypatch.setattr(assess_lead, "_mark_failed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fail")))
    monkeypatch.setattr(assess_lead, "_record_retry_error", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no retry")))

    lid = str(uuid.uuid4())
    result = assess_lead.assess_lead_task(lid)

    assert result["status"] == "parked_llm_unavailable"
    assert parked == [(lid, "DeepSeek 402: Insufficient Balance")]


def test_redrive_waits_while_paused_so_leads_keep_their_redrive_budget(monkeypatch, fake_redis):
    llm_breaker.open_breaker("DeepSeek 402: Insufficient Balance")
    monkeypatch.setattr(redrive, "_run", lambda: (_ for _ in ()).throw(AssertionError("must not redrive")))

    result = redrive.redrive_failed_assessments_task()

    assert result["skipped"] == "llm_unavailable"
