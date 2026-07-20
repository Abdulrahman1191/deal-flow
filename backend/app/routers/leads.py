from __future__ import annotations
from typing import Optional

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.event import LeadEvent
from app.models.lead import Lead
from app.models.user import User
from app.routers.assessments import _require_rating
from app.schemas.lead import LeadOut, LeadUpdate, LeadWithAssessment, PaginatedLeads, PitchDeckSyncResult
from app.services.auth import get_current_user, verify_webhook_signature
from app.services import copper_writer
from app.services.copper_echo_guard import is_recent_echo
from app.services.csv_export import build_leads_csv, effective_bucket
from app.services.events import EVENT_ARCHIVED, EVENT_ARCHIVED_NO_REPLY, EVENT_COPPER_UPDATED, log_event
from app.tasks.assess_lead import assess_lead_task

router = APIRouter(prefix="/leads", tags=["leads"])


async def _owner_for_assignee(db: AsyncSession, raw_payload: dict) -> str:
    """Map a Copper lead's assignee_id to the owning app user's email so
    webhook-created leads are scoped to the right person. Falls back to the
    configured account owner when the assignee isn't a known app user yet."""
    p = raw_payload.get("payload", raw_payload)
    assignee_id = p.get("assignee_id")
    if assignee_id:
        try:
            r = await db.execute(select(User).where(User.copper_user_id == int(assignee_id)))
            u = r.scalar_one_or_none()
            if u:
                return u.email
        except (ValueError, TypeError):
            pass
    return settings.owner_email


def _parse_copper_payload(payload: dict) -> dict:
    """
    Maps Copper CRM webhook payload to our Lead schema fields.
    Copper sends webhooks for the 'lead' resource type.
    """
    p = payload.get("payload", payload)

    emails = p.get("email", []) or []
    recipient_email = ""
    if isinstance(emails, list) and emails:
        recipient_email = emails[0].get("email", "")
    elif isinstance(emails, dict):
        recipient_email = emails.get("email", "")

    websites = p.get("websites", []) or []
    website = ""
    if isinstance(websites, list) and websites:
        website = websites[0].get("url", "")
    elif isinstance(websites, dict):
        website = websites.get("url", "")

    # Copper stores full name on the lead or on an associated person
    company_name = (
        p.get("company_name")
        or p.get("company", {}).get("name", "")
        or p.get("name", "Unknown")
    )

    tags = p.get("tags") or []
    stage = tags[0] if tags else p.get("status", {}).get("name") if isinstance(p.get("status"), dict) else None

    return {
        "copper_id": str(p.get("id", "")),
        "company_name": company_name,
        "website": website or None,
        "description": p.get("details") or p.get("description"),
        "stage": stage,
        "region": None,
        "founder_names": [p["name"]] if p.get("name") else None,
        "linkedin_urls": None,
        "raw_copper_data": {**p, "recipient_email": recipient_email},
    }


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_lead(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_copper_signature: Optional[str] = Header(default=None),
):
    body = await request.body()

    # Fail closed: reject whenever the signature header is missing or invalid,
    # or the shared secret isn't configured — never silently skip verification
    # (SECURITY_AUDIT.md F3).
    if not verify_webhook_signature(body, x_copper_signature or ""):
        raise HTTPException(status_code=401, detail="Missing or invalid webhook signature")

    raw = request.state.__dict__.get("_json") or {}
    try:
        import json
        raw = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Echo-loop guard: drop webhooks that mirror our own recent outbound writes.
    incoming_ids = raw.get("ids") or []
    incoming_id = str(incoming_ids[0]) if incoming_ids else ""
    drop, reason = is_recent_echo(incoming_id, raw.get("updated_attributes") or {})
    if drop:
        return {"status": "echo_dropped", "reason": reason}

    event = raw.get("event", "new")

    # Handle delete: archive the local lead so it disappears from the kanban.
    if event in ("delete", "deleted"):
        incoming_ids = raw.get("ids") or []
        copper_id = str(incoming_ids[0]) if incoming_ids else None
        if copper_id:
            result = await db.execute(select(Lead).where(Lead.copper_id == copper_id))
            lead = result.scalar_one_or_none()
            if lead and lead.status != "archived":
                lead.status = "archived"
                await log_event(db, lead.id, EVENT_ARCHIVED, {"reason": "deleted_in_copper"})
                await db.commit()
                return {"status": "archived", "lead_id": str(lead.id)}
        return {"status": "ignored", "event": event}

    # Handle update / edit: refresh the lead from Copper and merge changed fields
    # into our local row. Triggers reassessment only if assessment-relevant
    # fields (description, founder, website) actually changed.
    if event in ("update", "updated", "edit", "edited"):
        copper_id_from_event = str((raw.get("ids") or [None])[0] or "")
        if not copper_id_from_event:
            return {"status": "ignored", "event": event, "reason": "no_id"}

        # Pull authoritative current state from Copper
        from app.services.copper_service import fetch_lead_by_id, map_copper_lead
        try:
            fresh = fetch_lead_by_id(copper_id_from_event)
        except Exception as exc:
            return {"status": "fetch_failed", "error": repr(exc)}
        if not fresh:
            return {"status": "not_found_in_copper", "copper_id": copper_id_from_event}

        result = await db.execute(select(Lead).where(Lead.copper_id == copper_id_from_event))
        lead = result.scalar_one_or_none()
        if not lead:
            # Unknown locally — fall through to "new" logic via map_copper_lead
            lead_data = map_copper_lead(fresh)
            lead = Lead(**lead_data, owner_email=await _owner_for_assignee(db, fresh))
            db.add(lead)
            await db.commit()
            await db.refresh(lead)
            assess_lead_task.delay(str(lead.id))
            return {"lead_id": str(lead.id), "status": "queued_from_update"}

        # Diff: which assessment-relevant fields changed?
        fresh_data = map_copper_lead(fresh)
        watched = ("description", "company_name", "website", "founder_names", "stage", "region")
        material_change = any(
            getattr(lead, k) != fresh_data.get(k) for k in watched
        )
        for k, v in fresh_data.items():
            # Don't blow away our enriched fields (linkedin discovered, pitch deck, etc.)
            if k in ("company_linkedin_url",) and getattr(lead, k):
                continue
            if k == "raw_copper_data":
                # Merge instead of replace, preserve our `recipient_email` lookup etc.
                merged = (lead.raw_copper_data or {}).copy()
                merged.update(v or {})
                lead.raw_copper_data = merged
                continue
            setattr(lead, k, v)
        await log_event(db, lead.id, EVENT_COPPER_UPDATED, {"material": material_change})
        await db.commit()

        if material_change and lead.status not in ("archived", "approved"):
            lead.status = "pending"
            await db.commit()
            assess_lead_task.delay(str(lead.id))
            return {"lead_id": str(lead.id), "status": "synced_and_reassessing"}

        return {"lead_id": str(lead.id), "status": "synced"}

    if event not in ("new", "create", "created"):
        return {"status": "ignored", "event": event}

    lead_data = _parse_copper_payload(raw)

    # Deduplicate by copper_id
    if lead_data["copper_id"]:
        existing = await db.execute(select(Lead).where(Lead.copper_id == lead_data["copper_id"]))
        if existing.scalar_one_or_none():
            return {"status": "duplicate", "copper_id": lead_data["copper_id"]}

    lead = Lead(**lead_data, owner_email=await _owner_for_assignee(db, raw))
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    assess_lead_task.delay(str(lead.id))

    return {"lead_id": str(lead.id), "status": "queued"}


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_my_leads(user: User = Depends(get_current_user)):
    """Pull the current user's open-assigned Copper leads on demand (resolves +
    caches their Copper user id on first call). The board calls this on load so a
    new user's leads appear without waiting for the periodic beat sync."""
    from app.tasks.sync_copper import sync_user_copper_leads_task
    sync_user_copper_leads_task.delay(user.email)
    return {"status": "queued"}


@router.get("", response_model=PaginatedLeads)
async def list_leads(
    bucket: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    # Cap raised to 1000 so the dashboard can load the full pipeline in one page
    # (it groups all leads into YES/MAYBE/REJECT columns; there's no "load more").
    page_size: int = Query(default=20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Lead).options(selectinload(Lead.assessment)).where(Lead.owner_email == user.email)
    if status:
        query = query.where(Lead.status == status)
    else:
        # Default: hide archived and approved leads from the kanban.
        # Once an email is approved it's queued for sending — nothing left to decide.
        query = query.where(Lead.status.notin_(["archived", "approved"]))
    if search:
        query = query.where(Lead.company_name.ilike(f"%{search}%"))

    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()

    query = query.order_by(Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    leads = result.scalars().all()

    return PaginatedLeads(total=total, page=page, page_size=page_size, items=leads)


@router.get("/export")
async def export_leads(
    bucket: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Streams a CSV of the current user's leads, optionally filtered by
    bucket (YES/MAYBE/REJECT). Bucket resolution mirrors the kanban: a user
    override wins over the AI's original bucket. Returns headers-only CSV
    when nothing matches."""
    query = select(Lead).options(selectinload(Lead.assessment)).where(Lead.owner_email == user.email)
    result = await db.execute(query)
    leads = result.scalars().all()

    rows = (
        {
            "company_name": lead.company_name,
            "bucket": effective_bucket(lead.assessment),
            "confidence_score": lead.assessment.confidence_score if lead.assessment else None,
            "created_date": lead.created_at.isoformat(),
        }
        for lead in leads
        if not bucket or effective_bucket(lead.assessment) == bucket
    )
    csv_text = build_leads_csv(rows)

    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"},
    )


@router.get("/{lead_id}", response_model=LeadWithAssessment)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Lead).options(selectinload(Lead.assessment)).where(
            Lead.id == lead_id, Lead.owner_email == user.email
        )
    )
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: str,
    body: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(lead, field, value)
    await db.commit()
    await db.refresh(lead)
    return lead


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = "archived"
    await log_event(db, lead.id, EVENT_ARCHIVED, {"reason": "manual_delete"})
    await db.commit()

    # Mirror to Copper. Skip if already converted (Opportunity isn't in "open leads" anyway).
    if lead.copper_opportunity_id:
        print(
            f"[archive] lead {lead.id} already converted "
            f"(opportunity {lead.copper_opportunity_id}); skipping Copper archive write"
        )
    elif lead.copper_id:
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            copper_writer.archive_in_copper(lead.copper_id, existing_tags)
        except Exception as exc:
            print(f"[delete_lead] Copper write failed (local commit succeeded): {exc!r}")


@router.get("/archive/list")
async def list_archive(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns archived leads grouped by the action that archived them.
    Outcomes: sent_meeting_request, sent_rejection, sent_other, no_reply, manual.
    """
    result = await db.execute(
        select(Lead)
        .options(selectinload(Lead.assessment))
        .where(Lead.status == "archived", Lead.owner_email == user.email)
        .order_by(Lead.updated_at.desc())
    )
    leads = result.scalars().all()

    out = {
        "sent_meeting_request": [],
        "sent_rejection": [],
        "sent_other": [],
        "no_reply": [],
        "manual": [],
    }
    for lead in leads:
        # Look up the most recent archive event to determine the bucket.
        ev = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.lead_id == lead.id)
            .where(LeadEvent.event_type.in_(["archived", "archived_no_reply"]))
            .order_by(LeadEvent.created_at.desc())
            .limit(1)
        )
        last = ev.scalar_one_or_none()
        reason = (last.payload or {}).get("reason") if last and last.payload else None
        if last and last.event_type == "archived_no_reply":
            outcome = "no_reply"
        elif reason == "meeting_request":
            outcome = "sent_meeting_request"
        elif reason == "rejection":
            outcome = "sent_rejection"
        elif reason == "manual_delete":
            outcome = "manual"
        elif reason:
            outcome = "sent_other"
        else:
            outcome = "manual"

        out[outcome].append({
            "id": str(lead.id),
            "company_name": lead.company_name,
            "website": lead.website,
            "company_linkedin_url": lead.company_linkedin_url,
            "bucket": lead.assessment.user_override or lead.assessment.bucket if lead.assessment else None,
            "confidence_score": lead.assessment.confidence_score if lead.assessment else None,
            "copper_opportunity_id": lead.copper_opportunity_id,
            "archived_at": last.created_at.isoformat() if last else lead.updated_at.isoformat(),
        })
    return out


@router.post("/{lead_id}/archive-no-reply")
async def archive_no_reply(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Archive a lead without sending any email. Sets app status=archived AND moves
    the Copper Lead to Unqualified so it disappears from 'My Open Leads'.

    Gated behind the same rating mandate as /approve and /send: a lead with a
    latest assessment card that hasn't been rated 👍/👎 can't be archived either
    (defense-in-depth now that the frontend's one-click Skip button is gone). A
    lead with no assessment card yet has no recommendation to rate, so that case
    is allowed through.
    """
    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.status == "archived":
        return {"status": "already_archived"}

    from app.models.assessment import AssessmentCard
    card_result = await db.execute(
        select(AssessmentCard).where(AssessmentCard.lead_id == lead.id)
        .order_by(AssessmentCard.created_at.desc()).limit(1)
    )
    card = card_result.scalar_one_or_none()
    if card:
        _require_rating(card)

    lead.status = "archived"
    await log_event(db, lead.id, EVENT_ARCHIVED_NO_REPLY, {})
    await db.commit()

    if lead.copper_id and not lead.copper_opportunity_id:
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            copper_writer.archive_in_copper(lead.copper_id, existing_tags)
        except Exception as exc:
            print(f"[archive_no_reply] Copper write failed (local commit succeeded): {exc!r}")

    # Capture for training — Skip is an implicit REJECT (the human is saying
    # "I don't want to do anything with this lead"). Only meaningful when the
    # AI didn't already say REJECT.
    from app.services.override_capture import capture_override
    if card:
        await capture_override(
            db, lead=lead, card=card, human_bucket="REJECT", trigger="skip",
            acted_by_email=user.email,
        )

    return {"status": "archived", "outcome": "no_reply"}


@router.post("/{lead_id}/find-linkedin")
async def find_linkedin(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-run LinkedIn discovery for one lead. Tries website-scrape first,
    then the Tavily + LLM verifier. Persists the result on the Lead row."""
    from app.services import research

    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    found = research.scrape_linkedin_from_website(lead.website)
    source = "website_scrape"
    if not found and lead.company_name:
        found = research.find_linkedin_via_llm_search(
            company_name=lead.company_name,
            website=lead.website or "",
            founder_names=lead.founder_names,
            description=lead.description or "",
            region=lead.region or "",
        )
        source = "llm_search"

    if found:
        lead.company_linkedin_url = found
        await db.commit()
    return {
        "company_linkedin_url": found,
        "source": source if found else None,
    }


@router.post("/{lead_id}/sync-pitch-deck", response_model=PitchDeckSyncResult)
async def sync_pitch_deck(
    lead_id: str,
    force: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """On-demand per-lead Drive fetch+match+attach, with a structured
    diagnostic explaining exactly why a deck did or didn't attach. Unlike the
    30-min scheduled sweep (app.tasks.sync_pitch_decks.sync_pitch_decks_task),
    this runs inline for THIS lead only and always returns 200 with a
    diagnostic body -- never a bare 500.

    Idempotent: a lead that already has a Drive-matched deck returns
    "already attached" and queues nothing unless `force=true`. No rating
    gate -- fetching a deck isn't a disposition.
    """
    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    from app.tasks.sync_pitch_decks import sync_lead_pitch_deck
    return await sync_lead_pitch_deck(db, lead, force=force)


@router.get("/{lead_id}/events")
async def list_lead_events(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Returns the event timeline for a single lead, oldest first."""
    owns = await db.execute(
        select(Lead.id).where(Lead.id == lead_id, Lead.owner_email == user.email)
    )
    if not owns.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Lead not found")
    result = await db.execute(
        select(LeadEvent)
        .where(LeadEvent.lead_id == lead_id)
        .order_by(LeadEvent.created_at.asc())
    )
    events = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "payload": e.payload,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


@router.get("/{lead_id}/pitch-deck")
async def get_pitch_deck(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Redirects to the lead's pitch deck in the shared Google Drive folder.

    The Drive folder is shared with @raed.vc, so any platform-authenticated
    user can view the PDF directly in Drive's native viewer. Our backend
    never touches the file bytes.

    Drive file IDs get into the DB via scripts/sync_drive_to_db.py — run that
    once after uploading the PDFs to Drive, and on a cadence afterward if you
    keep adding new decks.
    """
    from fastapi.responses import RedirectResponse

    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if not lead.pitch_deck_drive_id:
        # Two paths to this: (a) lead has no deck on file at all, or (b) it
        # has one but the backfill script hasn't run yet. Distinguish in the
        # error message so we know which one.
        if lead.pitch_deck_filename:
            raise HTTPException(
                status_code=503,
                detail="Deck is on file but Drive file ID not yet synced — run scripts/sync_drive_to_db.py",
            )
        raise HTTPException(status_code=404, detail="No pitch deck on file")

    return RedirectResponse(
        url=f"https://drive.google.com/file/d/{lead.pitch_deck_drive_id}/view",
        status_code=307,
    )
