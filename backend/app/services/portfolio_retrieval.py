"""
Portfolio retrieval — Phase 2 of the LLM tuning roadmap.

Given a new lead, retrieves the 5-8 most relevant Raed portfolio companies
to include as historical precedents in the assess_lead prompt. The matched
rows carry the full retrospective context: Original Thesis (with kill
criterion), What Happened After, Lessons, Mental Models, Performance Status,
Hindsight Verdict, and the Disagreement flag.

This is the runtime implementation of the workflow documented in
raed_portfolio_v2_LLM_GUIDE.md (Steps 1-4).

Matching heuristic — weighted feature similarity, no embeddings yet:
  - sector keyword overlap (description + sector field) → 0..2 pts
  - region overlap                                       → 0..1.5 pts
  - thesis-tag intersection (inferred from lead text)   → 0..2 pts per match
  - disagreement-row bonus (highest training value)     → +1.0 pt
  - "TOO EARLY" rows get lightly down-weighted          → -0.5 pt

The bonus on disagreement rows is intentional — the LLM guide explicitly
calls these out as the highest-value training data. They teach the model
what we systematically *mis-underwrite*, which matters more than agreement
rows that may be diagnostic of luck.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional


_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "raed_portfolio_v2.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load + cache the portfolio JSON at first use."""
    with open(_DATA_PATH) as f:
        return json.load(f)


def all_companies() -> list[dict]:
    return _load()["companies"]


def vocabularies() -> dict[str, list[str]]:
    return _load()["vocabularies"]


# --- feature extraction ---

# Lightweight stop-word + filler list. We want distinguishing keywords from
# lead/portfolio descriptions to compute overlap, not match on filler.
_STOP = {
    "the", "a", "an", "and", "for", "with", "in", "on", "to", "of", "from",
    "by", "at", "is", "are", "was", "were", "be", "being", "been",
    "company", "startup", "platform", "based", "founded", "founder", "founders",
    "team", "product", "market", "industry", "solution", "service", "services",
    "saas", "app", "ai", "tech", "technology", "software",
    "our", "their", "they", "we", "you", "your", "this", "that",
}


def _keywords(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    tokens = re.findall(r"[A-Za-z]{4,}", text.lower())
    return {t for t in tokens if t not in _STOP}


_MENA_REGIONS = {
    "saudi", "ksa", "saudi arabia", "uae", "emirates", "egypt", "egyptian",
    "kuwait", "qatar", "bahrain", "oman", "jordan", "lebanon", "morocco",
    "tunisia", "iraq", "yemen", "palestine", "mena", "middle east", "gulf",
    "gcc",
}


def _region_set(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    t = text.lower()
    return {r for r in _MENA_REGIONS if r in t}


def _thesis_tags_of(company: dict) -> set[str]:
    return {t.strip().upper() for t in (company.get("thesis_tags") or []) if t}


# --- scoring ---


def _score(lead: dict, company: dict) -> float:
    score = 0.0

    # Sector / keyword overlap — both directions, weighted by intersection size.
    lead_kw = _keywords(
        (lead.get("description") or "") + " " + (lead.get("sector") or "")
    )
    comp_kw = _keywords(
        (company.get("description") or "") + " " + " ".join(
            t for t in (company.get("thesis_tags") or [])
        )
    )
    overlap = lead_kw & comp_kw
    # Cap at 2 pts so a single chatty description doesn't dominate.
    score += min(2.0, 0.4 * len(overlap))

    # Region overlap.
    lead_regions = _region_set(
        (lead.get("region") or "") + " " + (lead.get("description") or "")
    )
    comp_regions = _region_set(
        (company.get("description") or "") + " " + (company.get("why_now") or "")
    )
    if lead_regions & comp_regions:
        score += 1.5

    # Thesis-tag intersection (heuristic — lead doesn't have tags yet, infer
    # from description). Maps keywords to the 5 tag families.
    comp_tags = _thesis_tags_of(company)
    lead_text = ((lead.get("description") or "") + " "
                 + (lead.get("sector") or "")).lower()
    inferred_lead_tags = set()
    if any(kw in lead_text for kw in ("founder", "team", "ceo", "execution")):
        inferred_lead_tags.add("FOUNDER QUALITY")
    if any(kw in lead_text for kw in ("regulation", "license", "compliance",
                                       "regulator")):
        inferred_lead_tags.add("UNFAIR-ADVANTAGE")
    if any(kw in lead_text for kw in ("growth", "wave", "adoption", "trend",
                                       "tailwind")):
        inferred_lead_tags.add("MARKET-TAILWIND")
    if any(kw in lead_text for kw in ("first", "early", "novel", "new")):
        inferred_lead_tags.add("TIMING BET")
    score += 1.0 * len(comp_tags & inferred_lead_tags)

    # Disagreement-row bonus — these are the highest-signal training rows.
    if company.get("rationale_signals_disagreement"):
        score += 1.0

    # Lightly down-weight TOO-EARLY rows (outcome not yet observable).
    if company.get("performance_status") == "TOO EARLY":
        score -= 0.5

    return score


def find_similar(lead: dict, k: int = 6) -> list[dict]:
    """Returns the top-K most relevant portfolio companies for a given lead.

    Each item is the full row dict from raed_portfolio_v2.json, plus a
    `_score` field showing why it was ranked where it was.

    Args:
        lead: Dict with at least 'description'. Optionally 'sector', 'region'.
        k:    Number of precedents to return. Default 6 — small enough to
              fit in the assess prompt without ballooning tokens, large enough
              to span a few different patterns.
    """
    scored = []
    for c in all_companies():
        s = _score(lead, c)
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {**c, "_score": round(s, 2)}
        for s, c in scored[:k]
    ]


def format_for_prompt(precedents: list[dict]) -> str:
    """Render a list of retrieved precedents as the markdown block inserted
    into the assess_lead prompt. Dense but readable by the LLM — labelled
    fields, no filler.

    Each row is reduced to fields the guide says matter for retrieval:
    Original Thesis (with kill criterion), What Happened After, Lessons,
    Mental Models Applied, Performance Status, Hindsight Verdict, and the
    Disagreement flag.
    """
    if not precedents:
        return "(no similar precedents in the Raed portfolio)"

    parts = []
    for i, p in enumerate(precedents, 1):
        signals_summary = "; ".join(
            f"{s.get('type')}={s.get('direction')}({s.get('weight')})"
            for s in (p.get("signals") or [])
            if s.get("type")
        )
        flag = (
            " ⚑ DISAGREEMENT ROW (highest-signal training case)"
            if p.get("rationale_signals_disagreement") else ""
        )
        parts.append(
            f"### Precedent {i}: {p.get('company')} ({p.get('year')}){flag}\n"
            f"**Description:** {p.get('description')}\n"
            f"**Original Thesis:** {p.get('original_thesis')}\n"
            f"**Decision Rationale:** {p.get('decision_rationale')}\n"
            f"**Why Now (then):** {p.get('why_now')}\n"
            f"**What Happened After:** {p.get('what_happened_after')}\n"
            f"**Signals observed:** {signals_summary or '(none)'}\n"
            f"**Mental Models Applied:** {', '.join(p.get('mental_models_applied') or []) or '(none)'}\n"
            f"**Performance Status:** {p.get('performance_status')}    |    "
            f"**Hindsight Verdict:** {p.get('hindsight_verdict')}\n"
            f"**Lessons:** {p.get('lessons') or '(not captured)'}\n"
        )
    return "\n".join(parts)
