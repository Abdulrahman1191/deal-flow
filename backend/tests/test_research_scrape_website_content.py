"""
Tests for research.scrape_website_content (issue #144): the website-scraping
fallback that lets a deckless lead's public site substitute for a pitch deck.
Must be robust -- short timeout, at most landing page + /about, and NEVER
raise regardless of what goes wrong (no site, 404, timeout, unsafe host).
"""
from __future__ import annotations

import httpx

from app.services import research


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Stands in for httpx.Client -- maps URL -> response/exception so tests
    never touch the network."""

    def __init__(self, responses: dict):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, url):
        resp = self._responses.get(url)
        if resp is None:
            raise httpx.ConnectTimeout("simulated timeout")
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_no_website_returns_empty_string():
    assert research.scrape_website_content(None) == ""
    assert research.scrape_website_content("") == ""


def test_unsafe_host_returns_empty_string(monkeypatch):
    monkeypatch.setattr(research, "_is_safe_url", lambda url: False)
    assert research.scrape_website_content("https://169.254.169.254/") == ""


def test_extracts_visible_text_and_strips_script_and_style(monkeypatch):
    html = (
        "<html><head><style>body{color:red}</style></head><body>"
        "<script>var x = 1;</script>"
        "<main><h1>Acme Robotics</h1><p>We build proprietary sensors.</p></main>"
        "</body></html>"
    )
    responses = {
        "https://acme.test": _FakeResponse(200, html),
        "https://acme.test/about": _FakeResponse(404, ""),
    }
    monkeypatch.setattr(research, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(responses))

    text = research.scrape_website_content("https://acme.test")

    assert "Acme Robotics" in text
    assert "We build proprietary sensors." in text
    assert "var x = 1" not in text
    assert "color:red" not in text


def test_merges_landing_and_about_page_when_landing_is_short(monkeypatch):
    responses = {
        "https://acme.test": _FakeResponse(200, "<p>Acme Robotics.</p>"),
        "https://acme.test/about": _FakeResponse(200, "<p>Founded in 2022 by robotics engineers.</p>"),
    }
    monkeypatch.setattr(research, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(responses))

    text = research.scrape_website_content("https://acme.test")

    assert "Acme Robotics." in text
    assert "Founded in 2022 by robotics engineers." in text


def test_caps_output_length(monkeypatch):
    html = "<p>" + ("word " * 3000) + "</p>"
    responses = {"https://acme.test": _FakeResponse(200, html)}
    monkeypatch.setattr(research, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(responses))

    text = research.scrape_website_content("https://acme.test", max_chars=100)

    assert len(text) <= 100


def test_404_returns_empty_string(monkeypatch):
    responses = {
        "https://dead.test": _FakeResponse(404, "not found"),
        "https://dead.test/about": _FakeResponse(404, "not found"),
    }
    monkeypatch.setattr(research, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(responses))

    assert research.scrape_website_content("https://dead.test") == ""


def test_timeout_never_raises(monkeypatch):
    monkeypatch.setattr(research, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient({}))

    assert research.scrape_website_content("https://slow.test") == ""


def test_client_construction_failure_never_raises(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(research, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(httpx, "Client", _boom)

    assert research.scrape_website_content("https://acme.test") == ""


def test_website_without_scheme_gets_https_prefix(monkeypatch):
    responses = {"https://acme.test": _FakeResponse(200, "<p>Acme.</p>")}
    seen_urls = []

    def _is_safe_url(url):
        seen_urls.append(url)
        return True

    monkeypatch.setattr(research, "_is_safe_url", _is_safe_url)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(responses))

    text = research.scrape_website_content("acme.test")

    assert "Acme." in text
    assert seen_urls[0] == "https://acme.test"
