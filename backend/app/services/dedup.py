"""
Lead de-duplication core.

Shared by the manual CLI (scripts/dedupe_leads.py) and the scheduled Celery task
(app/tasks/dedupe_leads.py) so both behave identically.

Collapses active leads that share the same owner AND a normalized company name
down to a single canonical lead, ARCHIVING the rest. Grouping is scoped to
(owner_email, normalized company_name) -- the same company legitimately
assigned to two different partners in Copper is two distinct leads (distinct
copper_ids), not a duplicate, so cross-owner rows are never merged.

Before archiving, any deck or assessment the canonical lead is missing is
copied over from a loser so a merge never loses data (this is also what
unblocks pitch_deck.find_lead_match's ambiguity guard, which refuses to
attach a deck when 2+ leads share a normalized name).

Archive (not delete) keeps each duplicate's copper_id so a future Copper sync
won't re-import it, drops it off the board (which hides status='archived'),
and stays reversible.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentCard
from app.models.lead import Lead

_DECK_FIELDS = (
    "pitch_deck_drive_id",
    "pitch_deck_filename",
    "pitch_deck_text",
    "pitch_deck_ingested_at",
    "pitch_deck_s3",
)


def normalize_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _has_deck(lead) -> bool:
    """Mirrors the deckless check in tasks/sync_pitch_decks.py: a Drive-matched
    deck or a locally-ingested one both count as "has a deck"."""
    return bool(lead.pitch_deck_drive_id or lead.pitch_deck_text)


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


def _inherit_deck(canonical, losers: list) -> bool:
    """If canonical has no deck, copy one over from the best-ranked loser that
    has one. Returns True if a deck was inherited."""
    if _has_deck(canonical):
        return False
    for loser in losers:
        if _has_deck(loser):
            for field in _DECK_FIELDS:
                setattr(canonical, field, getattr(loser, field))
            return True
    return False


def _inherit_assessment(canonical, canonical_card, losers_with_cards: list) -> bool:
    """If canonical has no assessment card, reassign the best-ranked loser's
    card onto it (assessment_cards.lead_id is unique, so this only fires when
    canonical truly lacks one). Returns True if a card was inherited."""
    if canonical_card is not None:
        return False
    for loser, card in losers_with_cards:
        if card is not None:
            card.lead_id = canonical.id
            return True
    return False


async def dedupe_leads(db: AsyncSession, commit: bool = False) -> dict:
    """Find duplicate (owner_email, company name) groups among active leads;
    keep one canonical per group, archive the rest.

    Returns a report dict: {"active", "groups", "to_archive", "archived",
    "detail", "per_owner"}. With commit=False this is a pure dry-run (no
    writes). Idempotent: only groups non-archived leads, so a second run
    finds nothing.
    """
    leads = (await db.execute(select(Lead).where(Lead.status != "archived"))).scalars().all()

    groups: dict[tuple, list] = defaultdict(list)
    for l in leads:
        name_key = normalize_name(l.company_name)
        if name_key:
            groups[(l.owner_email, name_key)].append(l)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    detail: list[dict] = []
    per_owner: dict[str, int] = defaultdict(int)
    archived = 0
    for (owner_email, name_key), group in sorted(
        dup_groups.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])
    ):
        scored = []
        for l in group:
            card = await _latest_card(db, l.id)
            scored.append((_score(l, card), l, card))
        scored.sort(key=lambda x: x[0], reverse=True)
        canonical, canonical_card = scored[0][1], scored[0][2]
        losers = [(l, card) for _, l, card in scored[1:]]

        deck_inherited = _inherit_deck(canonical, [l for l, _ in losers])
        assessment_inherited = _inherit_assessment(canonical, canonical_card, losers)

        owner_key = owner_email or "(no owner)"
        per_owner[owner_key] += len(losers)
        detail.append({
            "owner_email": owner_key,
            "name": name_key,
            "keep": str(canonical.id),
            "keep_copper_id": canonical.copper_id,
            "archive": [str(l.id) for l, _ in losers],
            "archive_copper_ids": [l.copper_id for l, _ in losers],
            "deck_inherited": deck_inherited,
            "assessment_inherited": assessment_inherited,
        })
        if commit:
            for l, _ in losers:
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
        "per_owner": dict(per_owner),
    }
