"""
Lead de-duplication core.

Shared by the manual CLI (scripts/dedupe_leads.py) and the scheduled Celery task
(app/tasks/dedupe_leads.py) so both behave identically.

Collapses active leads that share a normalized company name down to a single
canonical lead, ARCHIVING the rest. Archive (not delete) keeps each duplicate's
copper_id so a future Copper sync won't re-import it, drops it off the board
(which hides status='archived'), and stays reversible.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentCard
from app.models.lead import Lead


def normalize_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


async def _latest_card(db: AsyncSession, lead_id):
    r = await db.execute(
        select(AssessmentCard).where(AssessmentCard.lead_id == lead_id)
        .order_by(AssessmentCard.created_at.desc()).limit(1)
    )
    return r.scalar_one_or_none()


def _score(lead, card) -> tuple:
    """Higher = better canonical. Prefer human-touched, then assessed, then
    most-complete, then has copper_id, then oldest (the original record)."""
    human = 1 if (card and (card.user_override or card.user_rating)) else 0
    assessed = 1 if card else 0
    has_deck = 1 if len(lead.pitch_deck_text or "") > 50 else 0
    has_research = 1 if (card and card.research_data) else 0
    has_copper = 1 if lead.copper_id else 0
    age = -(lead.created_at.timestamp() if lead.created_at else 0)
    return (human, assessed, has_deck, has_research, has_copper, age)


async def dedupe_leads(db: AsyncSession, commit: bool = False) -> dict:
    """Find duplicate-name groups among active leads; keep canonical, archive rest.

    Returns a report dict: {"active", "groups", "to_archive", "archived", "detail"}.
    With commit=False this is a pure dry-run (no writes). Idempotent: only groups
    non-archived leads, so a second run finds nothing.
    """
    leads = (await db.execute(select(Lead).where(Lead.status != "archived"))).scalars().all()

    groups: dict[str, list] = defaultdict(list)
    for l in leads:
        k = normalize_name(l.company_name)
        if k:
            groups[k].append(l)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    detail: list[dict] = []
    archived = 0
    for k, group in sorted(dup_groups.items()):
        scored = []
        for l in group:
            scored.append((_score(l, await _latest_card(db, l.id)), l))
        scored.sort(key=lambda x: x[0], reverse=True)
        canonical = scored[0][1]
        losers = [l for _, l in scored[1:]]
        detail.append({
            "name": k,
            "keep": str(canonical.id),
            "archive": [str(l.id) for l in losers],
        })
        if commit:
            for l in losers:
                l.status = "archived"
                try:
                    from app.services.events import log_event, EVENT_ARCHIVED
                    await log_event(db, l.id, EVENT_ARCHIVED,
                                    {"reason": "duplicate", "canonical": str(canonical.id)})
                except Exception as exc:
                    print(f"[dedup] event log skipped for {l.id}: {exc!r}")
                archived += 1
    if commit:
        await db.commit()

    return {
        "active": len(leads),
        "groups": len(dup_groups),
        "to_archive": sum(len(v) - 1 for v in dup_groups.values()),
        "archived": archived,
        "detail": detail,
    }
