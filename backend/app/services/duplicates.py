from __future__ import annotations
"""
Duplicate-lead detection -- owner-facing, flag-only safety net (issue #180).

This replaces the auto-prevention approach of #175: it NEVER merges, archives,
or reassigns anything on its own. It only reads leads and reports clusters
for a human to resolve via the existing archive endpoint, POST
/duplicates/reassign, or POST /duplicates/dismiss.

Root cause: a bug in the info@ email -> Copper ingest agent produced
same-second twin records for one application. Clustering catches that plus
ordinary re-submissions of the same company, across ALL owners -- the same
company landing under two different partners is the key case, so clustering
is deliberately firm-wide (unlike app.services.dedup, which is scoped to
(owner_email, name) because it actually merges).

Leads match (an edge in the cluster graph) on any of:
  - name: same dedup.py::normalize_name company name
  - email: same normalized applicant email, or (weaker) same domain + a
    similar local part
  - same_second: on top of a name/email match, the two leads were created
    within SAME_SECOND_WINDOW_SECONDS of each other -- the ingest agent's
    twin signature. Never fires on its own; two unrelated leads created in
    the same second are not a duplicate.

Clusters are formed via union-find over these pairwise edges (same full-scan
pattern already used by app.routers.leads::_find_merge_twin) so a chain of
matches (A~B on name, B~C on email) still lands in one cluster even though
A and C alone don't match.
"""
import difflib
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.duplicate_dismissal import DuplicateDismissal
from app.models.lead import Lead
from app.services.dedup import normalize_name

SAME_SECOND_WINDOW_SECONDS = 5
# Weak email signal: same domain + local parts at least this similar
# (difflib.SequenceMatcher ratio, 0..1).
_LOCAL_PART_SIMILARITY_THRESHOLD = 0.7

REASON_NAME = "name"
REASON_EMAIL = "email"
REASON_SAME_SECOND = "same_second"


def applicant_email(lead) -> str:
    """The lead's contact email, as captured by copper_service.map_copper_lead /
    leads._parse_copper_payload into raw_copper_data['recipient_email']."""
    raw = getattr(lead, "raw_copper_data", None) or {}
    return (raw.get("recipient_email") or "").strip().lower()


def _email_local_domain(email: str) -> tuple[str, str]:
    local, _, domain = email.partition("@")
    return local, domain


def _similar_local_part(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= _LOCAL_PART_SIMILARITY_THRESHOLD


def _match_reasons(a, b) -> set[str]:
    """Every reason lead `a` and `b` are judged to be the same company."""
    reasons: set[str] = set()

    name_a, name_b = normalize_name(a.company_name), normalize_name(b.company_name)
    if name_a and name_a == name_b:
        reasons.add(REASON_NAME)

    email_a, email_b = applicant_email(a), applicant_email(b)
    if email_a and email_a == email_b:
        reasons.add(REASON_EMAIL)
    elif email_a and email_b:
        local_a, domain_a = _email_local_domain(email_a)
        local_b, domain_b = _email_local_domain(email_b)
        if domain_a and domain_a == domain_b and _similar_local_part(local_a, local_b):
            reasons.add(REASON_EMAIL)

    if reasons and a.created_at and b.created_at:
        delta = abs((a.created_at - b.created_at).total_seconds())
        if delta <= SAME_SECOND_WINDOW_SECONDS:
            reasons.add(REASON_SAME_SECOND)

    return reasons


class _UnionFind:
    def __init__(self, items):
        self._parent = {item: item for item in items}

    def find(self, x):
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def cluster_key(lead_ids: Iterable) -> str:
    """Stable key for a cluster: the sorted lead-id set, pipe-joined. Changes
    whenever cluster membership changes -- the mechanism that lets a
    dismissed cluster reappear once a genuinely new lead joins it."""
    return "|".join(sorted(str(i) for i in lead_ids))


def find_duplicate_clusters(leads: list) -> list[dict]:
    """Pure clustering over an in-memory list of lead-like objects (needs
    .id, .company_name, .created_at, .raw_copper_data). Returns clusters of
    size >= 2, newest-first (by each cluster's newest lead), each:
        {"leads": [...newest first...], "match_reasons": [...], "signal_count": int}
    """
    uf = _UnionFind([l.id for l in leads])
    pair_reasons: dict[frozenset, set[str]] = defaultdict(set)

    for i, a in enumerate(leads):
        for b in leads[i + 1:]:
            reasons = _match_reasons(a, b)
            if reasons:
                uf.union(a.id, b.id)
                pair_reasons[frozenset((a.id, b.id))] |= reasons

    groups: dict = defaultdict(list)
    for lead in leads:
        groups[uf.find(lead.id)].append(lead)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_ids = {m.id for m in members}
        reasons: set[str] = set()
        for pair, r in pair_reasons.items():
            if pair <= member_ids:
                reasons |= r
        clusters.append({
            "leads": sorted(members, key=lambda l: l.created_at, reverse=True),
            "match_reasons": sorted(reasons),
            "signal_count": len(reasons),
        })

    clusters.sort(key=lambda c: c["leads"][0].created_at, reverse=True)
    return clusters


async def dismissed_cluster_keys(db: AsyncSession) -> set[str]:
    result = await db.execute(select(DuplicateDismissal.cluster_key))
    return {row[0] for row in result.all()}


async def list_duplicate_clusters(db: AsyncSession) -> list[dict]:
    """Firm-wide: every active (non-archived) lead, regardless of owner --
    cross-owner duplicates are the key case this exists to catch. Excludes
    clusters the owner has already dismissed (by exact member-set match)."""
    leads = (await db.execute(select(Lead).where(Lead.status != "archived"))).scalars().all()
    clusters = find_duplicate_clusters(leads)

    dismissed = await dismissed_cluster_keys(db)
    return [c for c in clusters if cluster_key(l.id for l in c["leads"]) not in dismissed]


async def dismiss_cluster(db: AsyncSession, lead_ids: list, dismissed_by: str) -> DuplicateDismissal:
    """Upsert-by-cluster_key: dismissing an already-dismissed cluster (same
    exact member set) returns the existing row instead of erroring."""
    key = cluster_key(lead_ids)
    existing = (
        await db.execute(select(DuplicateDismissal).where(DuplicateDismissal.cluster_key == key))
    ).scalar_one_or_none()
    if existing:
        return existing

    row = DuplicateDismissal(
        id=uuid.uuid4(),
        cluster_key=key,
        lead_ids=[str(i) for i in lead_ids],
        dismissed_by=dismissed_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
