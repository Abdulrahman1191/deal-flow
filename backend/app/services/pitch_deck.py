from __future__ import annotations
"""
Pitch deck ingestion helpers.

Workflow:
  1. User exports PDFs from Copper UI into settings.pitch_deck_inbox.
  2. Bulk script (or watcher daemon) walks the folder.
  3. For each PDF, match filename to a Lead by company_name (fuzzy).
  4. Extract text via pypdf, store on the lead row, queue re-assessment.
"""
import difflib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead

# Cap how much extracted text we persist in the DB. Pitch decks rarely run past
# 50K chars; we keep up to 30K to leave headroom for prompt + research data.
MAX_STORED_CHARS = 30_000


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
    """Best-effort text extraction. Returns empty string on parse errors.

    Strips characters Postgres' UTF-8 column can't store — chiefly U+0000 nulls
    which pypdf occasionally emits from corrupt or oddly-encoded PDFs.
    Without this strip, the INSERT fails with `CharacterNotInRepertoireError:
    invalid byte sequence for encoding "UTF8": 0x00`.
    """
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n\n".join(p for p in parts if p.strip())
        # PG can't store \x00 in text columns. Also strip other ASCII control
        # bytes except whitespace (\t \n \r) — they're either useless or breakage.
        text = text.replace("\x00", "")
        # Other C0 controls (0x01-0x08, 0x0B-0x0C, 0x0E-0x1F) → space.
        import re as _re
        text = _re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", " ", text)
        return text[:MAX_STORED_CHARS]
    except Exception as exc:
        print(f"[pitch_deck] failed to parse {path.name}: {exc!r}")
        return ""


def match_filename_to_lead(filename: str, leads: list[Lead]) -> Optional[Lead]:
    """Fuzzy-match a PDF filename to one of the supplied leads."""
    stem = Path(filename).stem  # "Hadawi.pdf" -> "Hadawi"
    norm_stem = _normalize_for_match(stem)
    if not norm_stem:
        return None

    by_norm = {_normalize_for_match(l.company_name): l for l in leads if l.company_name}

    # Exact normalized match first
    if norm_stem in by_norm:
        return by_norm[norm_stem]

    # Then fuzzy match (cutoff tuned for typical Copper filename variations)
    candidates = difflib.get_close_matches(norm_stem, list(by_norm.keys()), n=1, cutoff=0.82)
    return by_norm[candidates[0]] if candidates else None


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
