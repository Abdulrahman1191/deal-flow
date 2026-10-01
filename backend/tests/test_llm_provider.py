"""
LLM_PROVIDER switches claude_agent's text calls between DeepSeek and Gemini
(OpenAI-compatible endpoint) without touching call sites.

Gemini uses LLM_GEMINI_API_KEY, deliberately separate from GEMINI_API_KEY (deck
OCR). Gemini 3.x thinking tokens count against max_tokens, so small JSON calls
get a floor plus a reasoning_effort, and an empty answer raises instead of
being parsed. The 401 below is the body Google returned for raed-ai's key on
2026-10-01.
"""
from __future__ import annotations
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.services import claude_agent, llm_breaker

GEMINI_401_BODY = [{"error": {"code": 401, "status": "UNAUTHENTICATED", "message":
    "The bound service account is deleted or disabled. The service account bound to the API key must be active."}}]


class _Recorder:
    def __init__(self, content='{"is_match": true}', error=None, finish="stop"):
        self.calls, self.content, self.error, self.finish = [], content, error, finish
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content), finish_reason=self.finish)],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=8))


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value.encode()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(claude_agent, "_client", None)
    monkeypatch.setattr(llm_breaker, "_redis", _FakeRedis())
    monkeypatch.setattr(llm_breaker, "_local_open_until", 0.0)
    s = claude_agent.settings
    monkeypatch.setattr(s, "deep_seek_api", "ds-key")
    monkeypatch.setattr(s, "deepseek_model", "deepseek-chat")
    monkeypatch.setattr(s, "gemini_api_key", "ocr-key")
    monkeypatch.setattr(s, "gemini_model", "gemini-3.7-flash")
    monkeypatch.setattr(s, "llm_provider", "deepseek")
    monkeypatch.setattr(s, "llm_gemini_api_key", "")
    monkeypatch.setattr(s, "llm_gemini_model", "")
    monkeypatch.setattr(s, "llm_gemini_reasoning_effort", "low")
    monkeypatch.setattr(s, "llm_gemini_min_output_tokens", 2048)


def _gemini(monkeypatch, key="raed-ai-key"):
    monkeypatch.setattr(claude_agent.settings, "llm_provider", "gemini")
    monkeypatch.setattr(claude_agent.settings, "llm_gemini_api_key", key)


def _verify_call():
    return claude_agent.verify_pitch_deck_match("Acme Robotics", "warehouse arms", "Acme Robotics builds arms")


def test_default_provider_is_deepseek_and_calls_are_unchanged(monkeypatch):
    client = claude_agent._get_client()
    assert str(client.base_url).startswith("https://api.deepseek.com")
    assert client.api_key == "ds-key"
    rec = _Recorder()
    monkeypatch.setattr(claude_agent, "_get_client", lambda: rec)

    assert _verify_call() is True
    call = rec.calls[0]
    assert call["model"] == "deepseek-chat" and call["max_tokens"] == 200
    assert "extra_body" not in call


def test_gemini_uses_its_own_key_and_the_openai_compatible_endpoint(monkeypatch):
    _gemini(monkeypatch)
    client = claude_agent._get_client()
    assert str(client.base_url) == claude_agent.GEMINI_OPENAI_BASE_URL
    assert client.api_key == "raed-ai-key"  # not the deck-OCR GEMINI_API_KEY


def test_gemini_calls_get_its_model_a_token_floor_and_low_thinking(monkeypatch):
    _gemini(monkeypatch)
    rec = _Recorder()
    monkeypatch.setattr(claude_agent, "_get_client", lambda: rec)

    assert _verify_call() is True
    call = rec.calls[0]
    assert call["model"] == "gemini-3.7-flash"
    assert call["max_tokens"] == 2048  # was 200: thinking would eat it
    assert call["extra_body"] == {"reasoning_effort": "low"}
    assert call["response_format"] == {"type": "json_object"} and call["temperature"] == 0.0


def test_gemini_model_override_and_reasoning_effort_can_be_omitted(monkeypatch):
    _gemini(monkeypatch)
    monkeypatch.setattr(claude_agent.settings, "llm_gemini_model", "gemini-3-flash-preview")
    monkeypatch.setattr(claude_agent.settings, "llm_gemini_reasoning_effort", "")
    rec = _Recorder()
    monkeypatch.setattr(claude_agent, "_get_client", lambda: rec)

    _verify_call()
    assert rec.calls[0]["model"] == "gemini-3-flash-preview"
    assert "extra_body" not in rec.calls[0]


def test_larger_limits_are_kept(monkeypatch):
    _gemini(monkeypatch)
    rec = _Recorder()
    monkeypatch.setattr(claude_agent, "_get_client", lambda: rec)
    claude_agent._chat_completion(model="x", max_tokens=8096, messages=[])
    assert rec.calls[0]["max_tokens"] == 8096


def test_gemini_without_a_key_refuses_clearly(monkeypatch):
    _gemini(monkeypatch, key="")
    with pytest.raises(ValueError, match="LLM_GEMINI_API_KEY"):
        claude_agent._get_client()


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(claude_agent.settings, "llm_provider", "gpt")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        claude_agent._get_client()


def test_an_empty_answer_raises_instead_of_being_parsed(monkeypatch):
    _gemini(monkeypatch)
    rec = _Recorder(content="", finish="length")
    monkeypatch.setattr(claude_agent, "_get_client", lambda: rec)
    with pytest.raises(ValueError, match="no content.*length"):
        _verify_call()


def test_gemini_401_dead_service_account_opens_the_breaker(monkeypatch):
    _gemini(monkeypatch)
    request = httpx.Request("POST", claude_agent.GEMINI_OPENAI_BASE_URL + "chat/completions")
    err = APIStatusError("Error code: 401", response=httpx.Response(401, request=request, json=GEMINI_401_BODY),
                         body=GEMINI_401_BODY)
    rec = _Recorder(error=err)
    monkeypatch.setattr(claude_agent, "_get_client", lambda: rec)

    with pytest.raises(llm_breaker.LLMUnavailable, match="^gemini 401"):
        _verify_call()
    with pytest.raises(llm_breaker.LLMUnavailable):
        _verify_call()
    assert len(rec.calls) == 1


def test_each_call_logs_token_usage_for_cost_tracking(monkeypatch, capsys):
    _gemini(monkeypatch)
    monkeypatch.setattr(claude_agent, "_get_client", lambda: _Recorder())
    _verify_call()
    assert "[llm] provider=gemini model=gemini-3.7-flash prompt_tokens=120 completion_tokens=8" in capsys.readouterr().out
