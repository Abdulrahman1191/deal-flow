from __future__ import annotations
"""
Pitch deck ingestion helpers.

Workflow:
  1. User exports PDFs from Copper UI into settings.pitch_deck_inbox.
  2. Bulk script (or watcher daemon) walks the folder.
  3. For each PDF, match filename to a Lead by company_name (fuzzy).
  4. Extract text, store on the lead row, queue re-assessment.

Text extraction is layered (see extract_text_from_pdf):
  - PyMuPDF reads the embedded text layer. It handles Arabic font CMaps far
    better than pypdf, which mangles Arabic decks built on non-Unicode fonts
    into Latin-1 mojibake (e.g. "GþþÿN þþþþÿ" instead of real Arabic).
  - A garble guard rejects extractions dominated by junk characters.
  - When the text layer is absent (scanned decks) or garbled, we OCR the
    rendered pages with Tesseract (ara+eng). Arabic-first decks were the
    motivating case: a broken text layer meant the AI assessment scored them
    on noise.
"""
import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead

# Cap how much extracted text we persist in the DB. Pitch decks rarely run past
# 50K chars; we keep up to 30K to leave headroom for prompt + research data.
MAX_STORED_CHARS = 30_000

# --- Garble detection tunables ---------------------------------------------
# A clean text layer is dominated by printable ASCII (Latin letters, digits,
# punctuation) and/or Arabic. Broken-CMap extractions instead emit Latin-1 and
# symbol soup. We treat any char outside {printable-ASCII, Arabic, whitespace}
# as junk and reject the extraction once junk dominates.
_GARBLE_RATIO_THRESHOLD = 0.30   # >30% junk chars among non-whitespace => garbled
_MIN_USABLE_CHARS = 40           # below this the text layer is effectively absent

# --- OCR fallback tunables --------------------------------------------------
_OCR_LANGS = "ara+eng"
_OCR_DPI = 300                   # render resolution; 300 is the Tesseract sweet spot
_OCR_MAX_PAGES = 40              # cap work on very long decks

# Arabic Unicode blocks: base (0600-06FF), Supplement (0750-077F), Extended-A
# (08A0-08FF), Presentation Forms-A (FB50-FDFF) and -B (FE70-FEFF).
_ARABIC_RANGES = (
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
)


def _is_arabic_char(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _ARABIC_RANGES)


def _is_clean_text_char(ch: str) -> bool:
    """True for chars expected in a legit Arabic/English deck.

    Printable ASCII (0x20-0x7E) covers Latin letters, digits and punctuation;
    Arabic blocks cover Arabic script. Everything else — Latin-1 supplement
    (þ ÿ ð …), box-drawing, replacement chars, private-use glyphs — is the
    hallmark of a broken font/CMap extraction.
    """
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return True
    return _is_arabic_char(ch)


def _garble_ratio(text: str) -> float:
    """Fraction of non-whitespace chars that are junk (not clean ASCII/Arabic)."""
    meaningful = [c for c in text if not c.isspace()]
    if not meaningful:
        return 1.0
    junk = sum(1 for c in meaningful if not _is_clean_text_char(c))
    return junk / len(meaningful)


def _looks_garbled(text: str) -> bool:
    """True if the extraction is too short to be useful or junk-dominated."""
    if len(text.strip()) < _MIN_USABLE_CHARS:
        return True
    return _garble_ratio(text) > _GARBLE_RATIO_THRESHOLD


def _clean(text: str) -> str:
    """Strip bytes Postgres' UTF-8 text columns can't store.

    Chiefly U+0000 nulls, which pypdf/PyMuPDF occasionally emit from corrupt or
    oddly-encoded PDFs. Without this, the INSERT fails with
    `CharacterNotInRepertoireError: invalid byte sequence for encoding "UTF8": 0x00`.
    Other C0 control bytes (except tab/newline/cr) are collapsed to spaces.
    """
    if not text:
        return ""
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text


def _extract_pymupdf(path: Path) -> str:
    """Embedded text layer via PyMuPDF (fitz). Best Arabic CMap handling."""
    try:
        import fitz  # PyMuPDF; lazy so a missing wheel can't break module import
    except ImportError:
        return ""
    try:
        with fitz.open(str(path)) as doc:
            parts = [page.get_text() for page in doc]
        return "\n\n".join(p for p in parts if p.strip())
    except Exception as exc:
        print(f"[pitch_deck] PyMuPDF failed on {path.name}: {exc!r}")
        return ""


def _extract_pypdf(path: Path) -> str:
    """Embedded text layer via pypdf. Legacy fallback path."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n\n".join(p for p in parts if p.strip())
    except Exception as exc:
        print(f"[pitch_deck] pypdf failed on {path.name}: {exc!r}")
        return ""


def _extract_ocr(path: Path) -> str:
    """OCR the rendered pages with Tesseract (ara+eng).

    Used when no usable text layer exists (scanned decks) or the layer is
    garbled. Renders each page to a bitmap via PyMuPDF, then runs Tesseract.
    Degrades gracefully (returns "") if PyMuPDF/pytesseract/the tesseract binary
    or Arabic traineddata are unavailable, so a deployment without OCR support
    simply skips the deck rather than crashing.
    """
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        print(f"[pitch_deck] OCR deps unavailable ({exc}); skipping OCR for {path.name}")
        return ""

    import io

    try:
        parts: list[str] = []
        with fitz.open(str(path)) as doc:
            for page in doc[:_OCR_MAX_PAGES]:
                pix = page.get_pixmap(dpi=_OCR_DPI)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                parts.append(pytesseract.image_to_string(img, lang=_OCR_LANGS))
        return "\n\n".join(p for p in parts if p.strip())
    except pytesseract.TesseractNotFoundError:
        print(f"[pitch_deck] tesseract binary not found; skipping OCR for {path.name}")
        return ""
    except Exception as exc:
        print(f"[pitch_deck] OCR failed on {path.name}: {exc!r}")
        return ""


def _normalize_for_match(s: str) -> str:
    """Strip punctuation and collapse whitespace.

    Keeps Latin letters, digits, AND Arabic characters (U+0600..U+06FF +
    extended Arabic ranges) so that decks like 'مسار المحامي.pdf' actually
    match leads with Arabic names. The previous regex `[^A-Za-z0-9 ]` was
    nuking the entire Arabic alphabet, breaking matching for ~half the leads.
    """
    # Preserve Arabic (Unicode blocks U+0600-06FF, U+0750-077F, U+08A0-08FF,
    # U+FB50-FDFF, U+FE70-FEFF) plus Latin alphanumeric. Everything else
    # becomes whitespace, which is then collapsed.
    cleaned = re.sub(
        r"[^A-Za-z0-9؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿ ]",
        " ",
        s,
    )
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def extract_text_from_pdf(path: Path) -> str:
    """Best-effort text extraction, resilient to broken Arabic font CMaps.

    Strategy:
      1. Read the text layer with PyMuPDF (handles Arabic CMaps well). Use it if
         it's clean and substantial.
      2. Otherwise try pypdf, in case PyMuPDF choked on a quirk pypdf survives.
      3. If both text layers are absent or garbled, OCR the rendered pages.

    A garbled extraction (high ratio of junk chars — the Arabic mojibake bug) is
    never stored as-is: we re-extract via OCR, and if every method still yields
    garbage we return "" so the caller flags the deck rather than poisoning the
    AI assessment with noise.
    """
    layers = (
        ("pymupdf", _extract_pymupdf),
        ("pypdf", _extract_pypdf),
        ("ocr", _extract_ocr),
    )
    best_text = ""
    best_garble = 1.0
    for label, extractor in layers:
        text = _clean(extractor(path))
        if not _looks_garbled(text):
            if label == "ocr":
                print(f"[pitch_deck] {path.name}: text layer unusable, used OCR ({label})")
            return text[:MAX_STORED_CHARS]
        # Track the least-bad candidate purely for diagnostics.
        ratio = _garble_ratio(text)
        if text.strip() and ratio < best_garble:
            best_text, best_garble = text, ratio

    # Nothing produced clean text. Refuse to store garbage — flag instead.
    preview = (best_text.strip()[:80] or "<empty>")
    print(
        f"[pitch_deck] GARBLED/EMPTY extraction for {path.name} "
        f"(best junk ratio {best_garble:.2f}); not storing. preview: {preview!r}"
    )
    return ""


# --- Filename<->company-name matching tunables ------------------------------
# Real Drive filenames carry noise a bare company name never has: "Ailoo Pitch
# Deck.pdf", "ailoo_v2.pdf", "Ailoo Technologies.pdf". These tokens carry no
# identifying signal, so both the filename and every lead's company_name are
# stripped of them before comparing -- otherwise "Ailoo" (the lead) never
# matches "Ailoo Pitch Deck" (the file) closely enough to clear the fuzzy
# cutoff, and a real deck silently fails to attach.
_FILLER_TOKENS = {"pitch", "deck", "final", "draft", "presentation", "raed"}

# Trailing legal/entity suffixes: stripped from BOTH sides so "Ailoo" (lead)
# and "Ailoo Technologies.pdf" (file) normalize to the same key.
_ENTITY_SUFFIX_TOKENS = {"inc", "llc", "ltd", "fz", "technologies", "tech", "co", "company"}

_VERSION_TOKEN_RE = re.compile(r"^v\d{1,3}$")   # v1, v2, v10
_PURE_DIGIT_TOKEN_RE = re.compile(r"^\d+$")     # dates: 2024, 05, 20240501...

# Conservative: false matches (wrong lead) are worse than misses (no lead), so
# fuzzy matching only fires well above where genuinely different company
# names land (see backend/tests/test_pitch_deck.py for calibration, e.g.
# "Ailoo" vs "Aileen" must NOT clear this bar).
MATCH_THRESHOLD = 0.85


def _is_filler_token(token: str) -> bool:
    if token in _FILLER_TOKENS or token in _ENTITY_SUFFIX_TOKENS:
        return True
    if _VERSION_TOKEN_RE.match(token):
        return True
    if _PURE_DIGIT_TOKEN_RE.match(token):
        return True
    return False


def _normalize_company_key(s: str) -> str:
    """Normalize + strip filler/suffix/version/date tokens for matching.

    Applied identically to the filename stem and every lead's company_name so
    both sides of the comparison get the same treatment.
    """
    normalized = _normalize_for_match(s)
    if not normalized:
        return ""
    tokens = [t for t in normalized.split(" ") if t]
    filtered = [t for t in tokens if not _is_filler_token(t)]
    # If the whole name is filler tokens (shouldn't happen for a real company
    # name, but keeps a degenerate stem from becoming an empty key that could
    # spuriously equal another empty key), fall back to the unfiltered form.
    return " ".join(filtered) if filtered else normalized


@dataclass
class MatchCandidate:
    """A lead considered as a possible match, for diagnostics/reporting."""
    lead: Lead
    company_name: str
    score: float


@dataclass
class MatchResult:
    lead: Optional[Lead]
    candidates: list[MatchCandidate] = field(default_factory=list)


_MAX_REPORTED_CANDIDATES = 3


def find_lead_match(
    filename: str, leads: list[Lead], threshold: float = MATCH_THRESHOLD
) -> MatchResult:
    """Match a PDF filename to one of the supplied leads, with diagnostics.

    Exact match (after normalization) wins -- but only when exactly one lead's
    normalized company name matches the normalized filename. Two distinct
    leads can normalize to the same key (e.g. "Alpha Tech" and "Alpha Co" both
    strip to "alpha"), so an exact hit is treated exactly like the fuzzy path:
    ambiguity means no attach. Otherwise, fuzzy match only when exactly one
    lead clears `threshold` -- if two or more leads are plausible, or the best
    score is below threshold, this returns no match (attaching to the wrong
    lead is worse than leaving a file unmatched). Always returns the top few
    candidates (whether or not one was chosen) so callers can log/report why
    a file didn't attach.
    """
    stem = Path(filename).stem  # "Hadawi.pdf" -> "Hadawi"
    norm_stem = _normalize_company_key(stem)

    scored: list[MatchCandidate] = []
    exact_matches: list[MatchCandidate] = []
    for lead in leads:
        if not lead.company_name:
            continue
        norm_name = _normalize_company_key(lead.company_name)
        if not norm_name:
            continue
        score = difflib.SequenceMatcher(None, norm_stem, norm_name).ratio()
        candidate = MatchCandidate(lead=lead, company_name=lead.company_name, score=score)
        scored.append(candidate)
        if norm_stem and norm_name == norm_stem:
            exact_matches.append(candidate)

    scored.sort(key=lambda c: c.score, reverse=True)
    top_candidates = scored[:_MAX_REPORTED_CANDIDATES]

    if not norm_stem:
        return MatchResult(lead=None, candidates=top_candidates)

    if len(exact_matches) == 1:
        return MatchResult(lead=exact_matches[0].lead, candidates=top_candidates)
    if len(exact_matches) > 1:
        return MatchResult(lead=None, candidates=top_candidates)

    strong = [c for c in scored if c.score >= threshold]
    if len(strong) == 1:
        return MatchResult(lead=strong[0].lead, candidates=top_candidates)
    return MatchResult(lead=None, candidates=top_candidates)


def match_filename_to_lead(filename: str, leads: list[Lead]) -> Optional[Lead]:
    """Fuzzy-match a PDF filename to one of the supplied leads.

    Thin wrapper around find_lead_match() for callers that only need the
    result, not the diagnostic candidate list.
    """
    return find_lead_match(filename, leads).lead


async def ingest_pdf(db: AsyncSession, lead: Lead, path: Path) -> bool:
    """Extract + persist + queue re-assessment. Returns True on success."""
    text = extract_text_from_pdf(path)
    if not text:
        return False
    lead.pitch_deck_filename = path.name
    lead.pitch_deck_text = text
    lead.pitch_deck_ingested_at = datetime.now(timezone.utc)
    await db.commit()

    # Re-queue assessment so DeepSeek sees the deck. Done lazily here to avoid
    # circular imports at module load.
    from app.tasks.assess_lead import assess_lead_task
    assess_lead_task.delay(str(lead.id))
    return True
