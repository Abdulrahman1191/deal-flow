"""
Learned one-click reasons (issue #152).

Turns each user's own free-text `human_reason` / `human_reason_tags` on
`assessment_overrides` into short, deduped "chips" they can click instead of
retyping the same explanation. Grouped into the five decision contexts the
UI actually asks a reason for: rating up, rating down, and bucket YES /
MAYBE / REJECT.

Normalization approach: deterministic (lowercase/trim/punctuation-strip +
difflib fuzzy match), not an LLM pass. Reasons are short phrases, so
character-level similarity is a reliable-enough dedupe signal, and going
deterministic means zero added latency/cost per request, no LLM
flakiness in tests, and nothing to cache -- see the docstring on
`GET /overrides/my-reasons` in app/routers/overrides.py for why no cache
table is needed either.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Iterable, Optional

# Tuned against the issue's own dedupe examples ("not deep tech" / "Not deep
# tech enough" / "isn't deep tech" should collapse to one chip) while still
# keeping unrelated short reasons apart.
_SIMILARITY_THRESHOLD = 0.72

_CONTRACTION = re.compile(r"n't\b")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# "Other" is a placeholder tag meaning "see the free-text note" (see
# FeedbackModal.tsx) -- never a reason chip on its own.
_PLACEHOLDER_TAGS = {"other"}

CONTEXTS = ["rating_up", "rating_down", "bucket_yes", "bucket_maybe", "bucket_reject"]


def normalize_reason_text(text: str) -> str:
    """Lowercase/trim/punctuation-strip a reason string down to a dedupe
    key. Exported so callers merging personal + team chip lists can spot
    exact-normalized duplicates without re-running full cluster+rank."""
    t = text.strip().lower()
    t = _CONTRACTION.sub(" not", t)
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD


def context_for(trigger: Optional[str], human_bucket: Optional[str]) -> Optional[str]:
    """Which learned-reasons group a captured override row belongs to, or
    None if it carries no reusable reason (e.g. approve/skip auto-captures)."""
    if trigger == "confirm":
        return "rating_up"
    if trigger == "rate_down":
        return "rating_down"
    if trigger in ("override", "re-override") and human_bucket:
        bucket = human_bucket.strip().upper()
        if bucket == "YES":
            return "bucket_yes"
        if bucket == "MAYBE":
            return "bucket_maybe"
        if bucket == "REJECT":
            return "bucket_reject"
    return None


def _candidates(row) -> list[str]:
    out: list[str] = []
    for tag in (row.human_reason_tags or []):
        tag = (tag or "").strip()
        if tag and tag.lower() not in _PLACEHOLDER_TAGS:
            out.append(tag)
    note = (row.human_reason or "").strip()
    if note:
        out.append(note)
    return out


@dataclass
class _Cluster:
    normalized_rep: str
    raw_counts: dict = field(default_factory=dict)
    count: int = 0
    last_used_at: Optional[datetime] = None

    def add(self, raw: str, when: datetime) -> None:
        self.count += 1
        self.raw_counts[raw] = self.raw_counts.get(raw, 0) + 1
        if self.last_used_at is None or (when is not None and when > self.last_used_at):
            self.last_used_at = when

    def canonical_text(self) -> str:
        """The most frequent raw phrasing wins the cluster's display text;
        ties broken by shortest (usually the least redundant phrasing),
        then by first-seen order (dict preserves insertion order)."""
        best_raw, best_n = None, -1
        for raw, n in self.raw_counts.items():
            if best_raw is None or n > best_n or (n == best_n and len(raw) < len(best_raw)):
                best_raw, best_n = raw, n
        return (best_raw or "").strip().rstrip(".")


@dataclass
class LearnedReason:
    text: str
    count: int
    last_used_at: datetime


def _cluster(items: list[tuple[str, datetime]]) -> list[_Cluster]:
    clusters: list[_Cluster] = []
    for raw, when in items:
        norm = normalize_reason_text(raw)
        if not norm:
            continue
        match = next((c for c in clusters if _similar(norm, c.normalized_rep)), None)
        if match is None:
            match = _Cluster(normalized_rep=norm)
            clusters.append(match)
        match.add(raw, when)
    return clusters


def _score(cluster: _Cluster, now: datetime) -> float:
    """Frequency-first, recency as a tie-breaking boost: a reason used many
    times ranks above a once-off even if the once-off is more recent, but
    among similarly-frequent reasons the more recently used one wins."""
    if cluster.last_used_at is None:
        days = 3650.0
    else:
        days = max((now - cluster.last_used_at).total_seconds() / 86400.0, 0.0)
    recency = 1.0 / (1.0 + days / 14.0)
    return cluster.count * (0.6 + 0.4 * recency)


def build_learned_reasons(
    rows: Iterable, *, cap: int = 8, now: Optional[datetime] = None
) -> dict[str, list[LearnedReason]]:
    """Cluster+rank a batch of assessment_overrides rows (objects/Rows
    exposing `.trigger`, `.human_bucket`, `.human_reason`,
    `.human_reason_tags`, `.created_at`) into up to `cap` ranked chips per
    context. Deterministic and pure-Python -- no DB or network access."""
    now = now or datetime.now(timezone.utc)
    by_context: dict[str, list[tuple[str, datetime]]] = {}
    for row in rows:
        ctx = context_for(row.trigger, row.human_bucket)
        if not ctx:
            continue
        for cand in _candidates(row):
            by_context.setdefault(ctx, []).append((cand, row.created_at))

    result: dict[str, list[LearnedReason]] = {}
    for ctx, items in by_context.items():
        clusters = sorted(_cluster(items), key=lambda c: _score(c, now), reverse=True)
        result[ctx] = [
            LearnedReason(text=c.canonical_text(), count=c.count, last_used_at=c.last_used_at)
            for c in clusters[:cap]
        ]
    return result
