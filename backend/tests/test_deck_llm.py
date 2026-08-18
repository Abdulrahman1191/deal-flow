"""Tests for the multimodal-LLM deck extraction tier (app/services/deck_llm.py),
which replaced the Tesseract OCR tier on 2026-08-18."""
import json
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.services import deck_llm

PDF = b"%PDF-1.4 pretend this is a scanned Arabic deck"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")


class TestExtractText:
    def test_returns_model_text(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResponse(_ok_payload("Slide 1"))
        )
        assert deck_llm.extract_text(PDF) == "Slide 1"

    def test_concatenates_multiple_parts(self, monkeypatch):
        payload = {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload))
        assert deck_llm.extract_text(PDF) == "ab"

    def test_sends_pdf_as_inline_data_with_the_configured_model(self, monkeypatch):
        seen = {}

        def fake_urlopen(req, *a, **k):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data)
            return _FakeResponse(_ok_payload("x"))

        monkeypatch.setattr(settings, "gemini_model", "gemini-3.7-flash")
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        deck_llm.extract_text(PDF)

        assert "gemini-3.7-flash:generateContent" in seen["url"]
        parts = seen["body"]["contents"][0]["parts"]
        assert parts[1]["inline_data"]["mime_type"] == "application/pdf"
        # Transcription must not be creative -- a hallucinated figure here ends
        # up on a lead's assessment card.
        assert seen["body"]["generationConfig"]["temperature"] == 0

    def test_missing_key_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(settings, "gemini_api_key", "")
        with pytest.raises(deck_llm.DeckLLMUnavailable):
            deck_llm.extract_text(PDF)

    def test_oversized_pdf_raises_unavailable_without_calling_out(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("must not send an oversized payload")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(deck_llm.DeckLLMUnavailable):
            deck_llm.extract_text(b"x" * (deck_llm.MAX_PDF_BYTES + 1))

    def test_empty_pdf_returns_empty(self):
        assert deck_llm.extract_text(b"") == ""

    def test_http_error_degrades_to_empty(self, monkeypatch):
        def raise_http(*a, **k):
            raise urllib.error.HTTPError("u", 429, "rate limited", {}, None)

        monkeypatch.setattr(urllib.request, "urlopen", raise_http)
        assert deck_llm.extract_text(PDF) == ""

    def test_transport_error_degrades_to_empty(self, monkeypatch):
        def raise_timeout(*a, **k):
            raise TimeoutError("read timed out")

        monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
        assert deck_llm.extract_text(PDF) == ""

    def test_safety_blocked_response_degrades_to_empty(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResponse({"promptFeedback": {}})
        )
        assert deck_llm.extract_text(PDF) == ""
