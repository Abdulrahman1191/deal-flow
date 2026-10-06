"""
The DeepSeek breaker tells someone when it pauses assessments (2026-09-30 to 10-03 it paused silently).

One page per outage, worded as a warning with the fix, through reem's ops-alert path
(app/services/ops_alert.py). A breaker re-opening after a failed probe is the same outage.
"""
from __future__ import annotations

from app.services import llm_breaker, ops_alert


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value.encode()


def _setup(monkeypatch):
    pages = []
    monkeypatch.setattr(llm_breaker, "_redis", _FakeRedis())
    monkeypatch.setattr(llm_breaker, "_local_open_until", 0.0)
    monkeypatch.setattr(ops_alert, "page", lambda key, action, detail="", severity="warn": pages.append((key, action, detail, severity)) or True)
    return pages


def test_a_402_pages_once_with_the_top_up_fix(monkeypatch):
    pages = _setup(monkeypatch)
    llm_breaker.open_breaker("DeepSeek 402: Insufficient Balance (request_id: x)")
    assert len(pages) == 1
    key, action, detail, severity = pages[0]
    assert key == "deal-flow-deepseek-paused" and severity == "warn"
    assert "out of balance (402)" in action and "Top up the DeepSeek balance" in action and "every 15 minutes" in action
    assert "Insufficient Balance" in detail and "none are marked failed" in detail
    # Another caller hits the same 402 while it is open: same outage, no second page.
    llm_breaker.open_breaker("DeepSeek 402: Insufficient Balance (request_id: y)")
    assert len(pages) == 1


def test_a_401_pages_with_the_key_fix(monkeypatch):
    pages = _setup(monkeypatch)
    llm_breaker.open_breaker("DeepSeek 401: Authentication Fails")
    assert len(pages) == 1
    assert "rejected the API key (401)" in pages[0][1] and "DEEP_SEEK_API" in pages[0][1]


def test_unconfigured_route_logs_instead_and_never_raises(monkeypatch, capsys):
    monkeypatch.setattr(ops_alert.settings, "reem_ops_alert_url", "")
    monkeypatch.setattr(ops_alert.settings, "reem_internal_token", "")
    assert ops_alert.page("k", "do the thing", "why") is False
    assert "not configured, would page (k): do the thing | why" in capsys.readouterr().out


def test_configured_route_posts_the_ops_alert_contract(monkeypatch):
    sent = {}

    class _Res:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def _urlopen(req, timeout=None):
        import json
        sent.update(url=req.full_url, auth=req.headers.get("Authorization"), body=json.loads(req.data))
        return _Res()

    class _NowThread:
        def __init__(self, target=None, daemon=None): self.target = target
        def start(self): self.target()

    monkeypatch.setattr(ops_alert.settings, "reem_ops_alert_url", "http://reem.test/internal/ops/alert")
    monkeypatch.setattr(ops_alert.settings, "reem_internal_token", "tok")
    monkeypatch.setattr(ops_alert.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(ops_alert.threading, "Thread", _NowThread)
    assert ops_alert.page("deal-flow-deepseek-paused", "act", "det") is True
    assert sent["url"] == "http://reem.test/internal/ops/alert" and sent["auth"] == "Bearer tok"
    assert sent["body"] == {"key": "deal-flow-deepseek-paused", "action": "act", "detail": "det", "source": "deal-flow", "severity": "warn"}
