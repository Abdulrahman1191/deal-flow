from __future__ import annotations
"""
Pitch-deck text extraction via a multimodal LLM (Gemini).

Replaces the Tesseract OCR fallback that used to sit at the bottom of
extract_text_from_pdf's layer chain. That path rendered every page at 300 DPI
and ran `tesseract ara+eng` over the bitmaps -- pure CPU, several minutes per
deck, and Tesseract's OpenMP threads happily ate multiple cores. In a
`--pool=solo` Celery worker (one task at a time, beat embedded in the same
process) a single scanned deck therefore blocked assessments, Copper sync and
the outbox drain for as long as it ran. On 2026-08-18 that had the worker
pegged at 234% CPU with 12 leads queued behind it.

Sending the PDF to Gemini instead makes the same work network-bound: the
process sits in a socket read rather than burning cores, so the worker stays
responsive and prod's 4 CPUs stay available to everything else. It also reads
Arabic decks far better than Tesseract did -- Arabic-first decks with broken
font CMaps were the original motivation for OCR in the first place.

The PDF is uploaded as-is (no rasterising on our side). Gemini paginates and
does its own vision pass, so scanned decks with no text layer work exactly the
same as decks whose text layer is garbled.
"""
import base64
import json
import urllib.error
import urllib.request

from app.config import settings

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Deck PDFs are occasionally huge (image-heavy raster exports). The inline-data
# request path is documented up to ~20MB total payload; base64 inflates by 4/3,
# so cap the raw file well under that and let the caller fall back rather than
# firing a request the API will reject.
MAX_PDF_BYTES = 14_000_000

# Long enough for a 40-page image-heavy deck (a one-page scan measured ~15s),
# short enough that a hung request can't wedge the solo worker indefinitely.
REQUEST_TIMEOUT_SECONDS = 240

_PROMPT = (
    "Extract ALL text from this pitch deck, verbatim, in reading order, "
    "slide by slide. Preserve the original language exactly -- if the deck is "
    "in Arabic, output Arabic; do not translate, transliterate, summarise or "
    "reformat. Include headings, body text, figures, table contents and chart "
    "labels. Output only the extracted text, with no commentary and no "
    "markdown fences."
)


class DeckLLMUnavailable(RuntimeError):
    """Raised when the LLM path cannot run at all (no key, oversized file).

    Distinct from 'ran and produced nothing useful' so the caller can tell a
    misconfiguration apart from a genuinely unreadable deck.
    """


def _request_body(pdf_bytes: bytes) -> dict:
    return {
        "contents": [
            {
                "parts": [
                    {"text": _PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "application/pdf",
                            "data": base64.b64encode(pdf_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        # Deterministic: this is transcription, not generation. Any creativity
        # here is a hallucinated financial figure on a lead's card.
        "generationConfig": {"temperature": 0},
    }


def extract_text(pdf_bytes: bytes, *, filename: str = "deck.pdf") -> str:
    """Return the deck's text as read by Gemini, or "" if it produced nothing.

    Raises DeckLLMUnavailable when the path is not usable at all. Any other
    failure (HTTP error, malformed response) is logged and returns "" so the
    caller treats it like any other extractor that came up empty.
    """
    if not settings.gemini_api_key:
        raise DeckLLMUnavailable("GEMINI_API_KEY is not set")
    if not pdf_bytes:
        return ""
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise DeckLLMUnavailable(
            f"{filename} is {len(pdf_bytes)} bytes, over the {MAX_PDF_BYTES} inline limit"
        )

    url = _ENDPOINT.format(model=settings.gemini_model)
    req = urllib.request.Request(
        f"{url}?key={settings.gemini_api_key}",
        data=json.dumps(_request_body(pdf_bytes)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        # Read the error body defensively: this handler exists to guarantee a
        # graceful "" and must not itself raise. HTTPError.read() yields bytes
        # for a real response but not in every construction of the exception.
        try:
            raw = exc.read()
            body = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        except Exception:
            body = "<unreadable body>"
        print(f"[deck_llm] HTTP {exc.code} extracting {filename}: {body[:300]}")
        return ""
    except Exception as exc:
        print(f"[deck_llm] request failed for {filename}: {exc!r}")
        return ""

    candidates = payload.get("candidates") or []
    if not candidates:
        # Most often a safety block or an empty response on a corrupt PDF.
        print(f"[deck_llm] no candidates for {filename}: {str(payload)[:300]}")
        return ""

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    print(f"[deck_llm] {filename}: extracted {len(text)} chars via {settings.gemini_model}")
    return text
