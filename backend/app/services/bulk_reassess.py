from __future__ import annotations
"""
Bulk re-assessment (issue #203) -- re-runs assess_lead_task over many leads
at once, built for partners clearing the MAYBE pile (37% of a board). The
assessor runs at temperature=0, so identical inputs always reproduce the
identical verdict; this module exists to tell "a deck arrived / the
description changed / the website changed / the company name changed since
the last card" from "nothing changed, don't burn the tokens."

Issue #192 defines this input fingerprint but hasn't merged yet, so it's
defined here in the same terms: company name, description, pitch deck text,
and -- for deck-less leads only, mirroring assess_lead._run's own rule that
website content is scraped exclusively when there's no deck -- the lead's
current scraped website content. A card written before this column existed
has `input_fingerprint is None`, which is always treated as "changed" rather
than guessed.

Shared by POST /leads/bulk-reassess/preview and POST /leads/bulk-reassess so
the two can never disagree about which leads match / have changed inputs --
same split-brain concern app/services/bulk_reassign.py solves for reassignment.
"""
import asyncio
import hashlib
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.event import LeadEvent
from app.models.lead import Lead
from app.services import research
from app.services.csv_export import effective_bucket

# A lead already queued for an assessment attempt. Queuing it again would
# race the in-flight run and double-count assessment_attempts / events
# (issue #203 bullet 5).
IN_FLIGHT_STATUSES = ("pending", "processing")

# Bulk-reassess runs inside the request/response cycle (the partner is
# looking at a preview before deciding), so website scrapes for a batch run
# concurrently, bounded, rather than one network round trip at a time.
_MAX_CONCURRENT_SCRAPES = 10


class NotOwnedError(Exception):
    """Raised when an explicit lead_ids request includes a lead that isn't
    the caller's own. The router turns this into a 403 for the whole
    request -- never a partial skip, since silently dropping someone else's
    lead would look like "my lead didn't match" rather than "you tried to
    touch someone else's board."""


def compute_fingerprint(
    company_name: Optional[str],
    description: Optional[str],
    pitch_deck_text: Optional[str],
    website_content: str,
) -> str:
    raw = "\x1f".join([company_name or "", description or "", pitch_deck_text or "", website_content or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _website_content_map(leads: list[Lead]) -> dict[uuid.UUID, str]:
    """lead.id -> current scraped website content, "" for leads with a deck
    or no website -- mirrors assess_lead._run's own has_deck gate so this
    never flags a "changed" input the assessor would never actually read."""
    sem = asyncio.Semaphore(_MAX_CONCURRENT_SCRAPES)

    async def _one(lead: Lead) -> tuple[uuid.UUID, str]:
        if lead.pitch_deck_text or not lead.website:
            return lead.id, ""
        async with sem:
            content = await asyncio.to_thread(research.scrape_website_content, lead.website)
        return lead.id, content

    pairs = await asyncio.gather(*[_one(lead) for lead in leads])
    return dict(pairs)


async def classify(leads: list[Lead]) -> dict[uuid.UUID, bool]:
    """lead.id -> True if its assessor inputs changed since the latest card
    (or it has never been assessed at all)."""
    website_by_id = await _website_content_map(leads)
    changed: dict[uuid.UUID, bool] = {}
    for lead in leads:
        current_fp = compute_fingerprint(
            lead.company_name, lead.description, lead.pitch_deck_text, website_by_id[lead.id]
        )
        card = lead.assessment
        changed[lead.id] = card is None or card.input_fingerprint != current_fp
    return changed


async def resolve_candidates(
    db: AsyncSession,
    owner_email: str,
    lead_ids: Optional[list[str]] = None,
    bucket: Optional[str] = None,
    assessed_before: Optional[datetime] = None,
) -> list[Lead]:
    """The leads a bulk-reassess call would act on, sorted by id for a
    deterministic batch. Never includes an archived lead -- a final
    disposition has nothing left for the assessor to reconsider.

    Either `lead_ids` is given (explicit selection -- every id must belong to
    `owner_email` or this raises NotOwnedError) or the `bucket` /
    `assessed_before` filter is applied over the owner's whole board. A
    `lead_ids` entry that simply doesn't exist is silently excluded (not an
    ownership violation -- there's no lead to own or not own).
    """
    if lead_ids:
        try:
            uuids = [uuid.UUID(str(lid)) for lid in lead_ids]
        except ValueError as exc:
            raise ValueError(f"invalid lead id: {exc}") from exc
        result = await db.execute(
            select(Lead).options(selectinload(Lead.assessment)).where(Lead.id.in_(uuids))
        )
        leads = result.scalars().all()
        for lead in leads:
            if lead.owner_email != owner_email:
                raise NotOwnedError(f"lead {lead.id} is not owned by {owner_email}")
        candidates = [lead for lead in leads if lead.status != "archived"]
    else:
        result = await db.execute(
            select(Lead).options(selectinload(Lead.assessment)).where(Lead.owner_email == owner_email)
        )
        leads = result.scalars().all()
        candidates = []
        for lead in leads:
            if lead.status == "archived":
                continue
            if bucket and effective_bucket(lead.assessment) != bucket:
                continue
            if assessed_before is not None:
                card = lead.assessment
                if card and card.created_at and card.created_at >= assessed_before:
                    continue
            candidates.append(lead)

    return sorted(candidates, key=lambda l: l.id)


def summarize_progress(batch_id: uuid.UUID, rows: list[tuple[LeadEvent, Lead]]) -> dict:
    """Reduces the batch's `bulk_reassess_queued` events + each lead's
    current state into the progress counts GET
    /leads/bulk-reassess/{batch_id} reports. `bucket_changed` compares the
    effective bucket captured in the event payload at queue time
    (`bucket_before`) against the lead's effective bucket now -- the only
    honest measure of whether the batch was worth running -- and is only
    computed for leads that have actually finished (assessed or archived
    mid-run); a lead still pending or dead-lettered to failed hasn't
    produced a new verdict to compare yet."""
    queued = len(rows)
    assessed = pending = failed = bucket_changed = bucket_unchanged = 0
    for event, lead in rows:
        if lead.status in IN_FLIGHT_STATUSES or lead.status == "awaiting_deck":
            pending += 1
            continue
        if lead.status == "failed":
            failed += 1
            continue
        assessed += 1
        bucket_before = (event.payload or {}).get("bucket_before")
        bucket_after = effective_bucket(lead.assessment)
        if bucket_before != bucket_after:
            bucket_changed += 1
        else:
            bucket_unchanged += 1
    return {
        "batch_id": batch_id,
        "queued": queued,
        "assessed": assessed,
        "pending": pending,
        "failed": failed,
        "bucket_changed": bucket_changed,
        "bucket_unchanged": bucket_unchanged,
    }
