from __future__ import annotations
import uuid
from datetime import date, datetime, time, timezone
from typing import List, Optional

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.bulk_rejection import BulkRejectionBatchItem
from app.models.event import LeadEvent
from app.models.lead import Lead
from app.models.user import User
from app.routers.assessments import _require_rating
from app.schemas.lead import (
    BulkArchiveRequest,
    BulkArchiveResult,
    BulkReassessPreviewRequest,
    BulkReassessPreviewResult,
    BulkReassessProgress,
    BulkReassessRequest,
    BulkReassessResult,
    BulkReassignPreviewRequest,
    BulkReassignPreviewResult,
    BulkReassignRequest,
    BulkReassignResult,
    BulkSendRejectionBatchItemOut,
    BulkSendRejectionBatchStatus,
    BulkSendRejectionPreviewItem,
    BulkSendRejectionPreviewRequest,
    BulkSendRejectionPreviewResult,
    BulkSendRejectionRequest,
    BulkSendRejectionResult,
    BulkSendRejectionSkipped,
    LeadOut,
    LeadUpdate,
    LeadWithAssessment,
    PaginatedLeads,
    PitchDeckSyncResult,
)
from app.services.auth import (
    block_if_impersonating,
    effective_owner_email,
    get_current_user,
    is_owner,
    verify_webhook_signature,
)
from app.services import bulk_reassess, bulk_reassign, bulk_rejection, claude_agent, copper_writer, llm_breaker
from app.services.copper_echo_guard import is_recent_echo
from app.services.csv_export import build_leads_csv, effective_bucket
from app.services.dedup import normalize_name
from app.services.events import (
    EVENT_ARCHIVED,
    EVENT_ARCHIVED_NO_REPLY,
    EVENT_BULK_REASSESS_QUEUED,
    EVENT_COPPER_RECORD_VANISHED,
    EVENT_COPPER_UPDATED,
    EVENT_REASSIGNED,
    log_event,
)
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


async def _resolve_reassignment_owner(db: AsyncSession, fresh_payload: dict, lead: Lead) -> Optional[str]:
    """Resolves a webhook `update` payload's assignee_id to a known app
    user's email, for the reassignment case only. Returns None -- meaning
    "leave owner_email alone" -- when there's no assignee_id on the payload,
    it already matches the lead's current owner, or it doesn't resolve to a
    known user. Unlike `_owner_for_assignee` (used for brand-new leads), this
    never falls back to settings.owner_email: an unresolved assignee on an
    *existing* lead must leave owner_email untouched (today's
    reconcile_ownership behaviour), not blank it to the default owner.
    """
    assignee_id = fresh_payload.get("assignee_id")
    if not assignee_id:
        return None
    try:
        result = await db.execute(select(User).where(User.copper_user_id == int(assignee_id)))
    except (ValueError, TypeError):
        return None
    user = result.scalar_one_or_none()
    if not user or user.email == getattr(lead, "owner_email", None):
        return None
    return user.email


async def _find_merge_twin(db: AsyncSession, lead: Lead) -> Optional[Lead]:
    """A Copper `delete` fires for the losing side of a merge as well as for
    a genuine delete. Look for another active local lead that is plausibly
    the surviving record -- same normalized company name, or the same
    contact email -- so a merge doesn't hide a company that's still live
    under the other Copper id (issue #174: an audit found 36 leads silently
    lost this exact way). Returns None when nothing matches, meaning this
    delete is a true delete rather than a merge."""
    name_key = normalize_name(lead.company_name)
    email = ((lead.raw_copper_data or {}).get("recipient_email") or "").strip().lower()
    if not name_key and not email:
        return None

    result = await db.execute(
        select(Lead).where(Lead.status != "archived", Lead.id != lead.id)
    )
    for candidate in result.scalars().all():
        if name_key and normalize_name(candidate.company_name) == name_key:
            return candidate
        if email:
            candidate_email = ((candidate.raw_copper_data or {}).get("recipient_email") or "").strip().lower()
            if candidate_email and candidate_email == email:
                return candidate
    return None


async def _archive_deleted_lead(db: AsyncSession, copper_id: str) -> Optional[dict]:
    """Archives a single local lead for one id out of a Copper `delete`
    webhook's `ids` list (Copper batches merges/bulk-deletes into one
    notification, so this runs once per id -- see `ingest_lead`). Returns
    None when there's nothing to do: unknown copper_id, or already
    archived."""
    result = await db.execute(select(Lead).where(Lead.copper_id == copper_id))
    lead = result.scalar_one_or_none()
    if not lead or lead.status == "archived":
        return None

    twin = await _find_merge_twin(db, lead)
    if twin:
        lead.status = "archived"
        await log_event(
            db, lead.id, EVENT_ARCHIVED,
            {"reason": "merged_in_copper", "surviving_lead_id": str(twin.id)},
        )
        await db.commit()
        return {
            "status": "archived",
            "reason": "merged_in_copper",
            "lead_id": str(lead.id),
            "surviving_lead_id": str(twin.id),
        }

    # No twin -- this is either a true delete, or a merge whose surviving
    # record we can't identify. Either way the lead disappearing must be
    # auditable, not silent: archive as before, but also log
    # copper_record_vanished so it surfaces via GET /leads/orphans instead
    # of just vanishing.
    lead.status = "archived"
    await log_event(db, lead.id, EVENT_ARCHIVED, {"reason": "deleted_in_copper"})
    await log_event(
        db, lead.id, EVENT_COPPER_RECORD_VANISHED,
        {"copper_id": copper_id, "company_name": lead.company_name},
    )
    await db.commit()
    return {"status": "archived", "reason": "deleted_in_copper", "lead_id": str(lead.id)}


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


async def _import_new_copper_lead(db: AsyncSession, copper_id: str) -> dict:
    """Import one lead named by a Copper `new` notification. Copper's
    notification body carries only `ids` (no lead fields), so the lead is
    fetched from Copper -- the same import the `update` branch does for an
    unknown id. Idempotent: an id already on a row, including one the polling
    sync inserted concurrently, is reported as a duplicate, never a 500."""
    from app.services.copper_service import fetch_lead_by_id, map_copper_lead

    existing = await db.execute(select(Lead).where(Lead.copper_id == copper_id))
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "copper_id": copper_id}
    try:
        fresh = fetch_lead_by_id(copper_id)
    except Exception as exc:
        # Transient Copper failure: a non-2xx makes Copper redeliver, and the
        # redelivery is idempotent via the duplicate check above.
        raise HTTPException(status_code=503, detail=f"Copper fetch failed for {copper_id}: {exc!r}")
    if not fresh:
        return {"status": "not_found_in_copper", "copper_id": copper_id}

    lead = Lead(**map_copper_lead(fresh), owner_email=await _owner_for_assignee(db, fresh))
    db.add(lead)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return {"status": "duplicate", "copper_id": copper_id}
    await db.refresh(lead)
    if lead.applied_at is None:
        # map_copper_lead couldn't resolve applied_at from Copper's
        # date_created -- fall back to our own import timestamp, the same
        # rule LeadOut.applied_at used before it became a real column.
        lead.applied_at = lead.created_at
        await db.commit()
    assess_lead_task.delay(str(lead.id))
    return {"lead_id": str(lead.id), "status": "queued", "copper_id": copper_id}


async def _sync_updated_copper_lead(db: AsyncSession, copper_id_from_event: str, updated_attributes: dict) -> dict:
    """Refresh one lead named by a Copper `update` notification. Copper
    aggregates notifications, so one body's `ids` can name several leads; the
    ingest route calls this once per id. The echo guard runs per id because
    its registry check is keyed by Copper id."""
    drop, reason = is_recent_echo(copper_id_from_event, updated_attributes)
    if drop:
        return {"status": "echo_dropped", "reason": reason, "copper_id": copper_id_from_event}

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
        try:
            await db.commit()
        except IntegrityError:
            # The polling sync inserted it between our lookup and commit.
            await db.rollback()
            return {"status": "duplicate", "copper_id": copper_id_from_event}
        if lead.applied_at is None:
            # map_copper_lead couldn't resolve applied_at from Copper's
            # date_created -- fall back to our own import timestamp (the
            # commit above already populated created_at via Postgres'
            # implicit RETURNING), same rule the pre-column LeadOut.applied_at
            # used.
            lead.applied_at = lead.created_at
            await db.commit()
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
        if k == "applied_at" and v is None:
            # Never null out an already-resolved applied_at (e.g. the
            # created_at fallback set at import) just because this refresh's
            # raw payload didn't carry date_created.
            continue
        if k == "raw_copper_data":
            # Merge instead of replace, preserve our `recipient_email` lookup etc.
            merged = (lead.raw_copper_data or {}).copy()
            merged.update(v or {})
            lead.raw_copper_data = merged
            continue
        setattr(lead, k, v)

    # Reassignment: assignee_id isn't in `watched` above on purpose --
    # moving a lead between partners is an ownership change, not an
    # assessment-relevant edit, and must never re-queue/re-verdict it.
    new_owner = await _resolve_reassignment_owner(db, fresh, lead)
    if new_owner:
        from_owner = lead.owner_email
        lead.owner_email = new_owner
        await log_event(db, lead.id, EVENT_REASSIGNED,
                         {"from_owner": from_owner, "to_owner": new_owner, "source": "webhook"})

    await log_event(db, lead.id, EVENT_COPPER_UPDATED, {"material": material_change})
    await db.commit()

    if material_change and lead.status not in ("archived", "approved"):
        lead.status = "pending"
        lead.assessment_attempts = 0
        await db.commit()
        assess_lead_task.delay(str(lead.id))
        return {"lead_id": str(lead.id), "status": "synced_and_reassessing"}

    return {"lead_id": str(lead.id), "status": "synced"}


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_lead(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_copper_signature: Optional[str] = Header(default=None),
    x_copper_webhook_token: Optional[str] = Header(default=None),
):
    body = await request.body()

    # Fail closed: reject unless the X-Copper-Webhook-Token header (what Copper
    # actually sends) or an X-Copper-Signature HMAC is valid, or when the shared
    # secret isn't configured — never silently skip verification
    # (SECURITY_AUDIT.md F3).
    if not verify_webhook_signature(body, x_copper_signature or "", x_copper_webhook_token or ""):
        raise HTTPException(status_code=401, detail="Missing or invalid webhook signature")

    raw = request.state.__dict__.get("_json") or {}
    try:
        import json
        raw = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Echo-loop guard: drop webhooks that mirror our own recent outbound writes.
    # Multi-id notifications are echo-checked per id in _sync_updated_copper_lead:
    # one id's echo must not drop the other leads in the same notification.
    incoming_ids = raw.get("ids") or []
    if len(incoming_ids) <= 1:
        incoming_id = str(incoming_ids[0]) if incoming_ids else ""
        drop, reason = is_recent_echo(incoming_id, raw.get("updated_attributes") or {})
        if drop:
            return {"status": "echo_dropped", "reason": reason}

    event = raw.get("event", "new")

    # Handle delete: archive the local lead so it disappears from the kanban.
    # Copper fires `delete` both for a true delete and for the losing side of
    # a merge -- a merge must not archive-and-forget, since the company is
    # still live under the other Copper id (issue #174). Copper also
    # aggregates notifications, so a single webhook's `ids` can list several
    # records (e.g. a human bulk-merging duplicate pairs) -- every id gets
    # the same treatment so a batched delete never silently drops leads after
    # the first.
    if event in ("delete", "deleted"):
        incoming_ids = raw.get("ids") or []
        results = []
        for raw_id in incoming_ids:
            outcome = await _archive_deleted_lead(db, str(raw_id))
            if outcome:
                results.append(outcome)
        if not results:
            return {"status": "ignored", "event": event}
        if len(results) == 1:
            return results[0]
        return {"status": "archived", "count": len(results), "results": results}

    # Handle update / edit: refresh the lead from Copper and merge changed fields
    # into our local row. Triggers reassessment only if assessment-relevant
    # fields (description, founder, website) actually changed.
    if event in ("update", "updated", "edit", "edited"):
        update_ids = [str(i) for i in (raw.get("ids") or []) if str(i or "").strip()]
        if not update_ids:
            return {"status": "ignored", "event": event, "reason": "no_id"}
        updated_attributes = raw.get("updated_attributes") or {}
        results = [await _sync_updated_copper_lead(db, cid, updated_attributes) for cid in update_ids]
        if len(results) == 1:
            return results[0]
        return {"status": "processed", "count": len(results), "results": results}

    if event not in ("new", "create", "created"):
        return {"status": "ignored", "event": event}

    # A real Copper notification is {ids, type, event, subscription_id,
    # timestamp} with no lead fields; parsing it as a lead gave copper_id=""
    # and company "Unknown" -- the first one inserted a junk row, every later
    # one hit leads_copper_id_key and 500'd. Import each id from Copper
    # instead. A body that carries the lead itself (`payload` or `id`) keeps
    # the direct-parse path below.
    if "payload" not in raw and not str(raw.get("id") or "").strip():
        new_ids = [str(i) for i in (raw.get("ids") or []) if str(i or "").strip()]
        if not new_ids:
            return {"status": "ignored", "event": event, "reason": "no_id"}
        results = [await _import_new_copper_lead(db, cid) for cid in new_ids]
        if len(results) == 1:
            return results[0]
        return {"status": "processed", "count": len(results), "results": results}

    lead_data = _parse_copper_payload(raw)

    # Never insert a lead without a Copper id: "" is a real value to the unique
    # constraint, so a second one would 500.
    if not lead_data["copper_id"]:
        return {"status": "ignored", "event": event, "reason": "no_id"}

    # Deduplicate by copper_id
    existing = await db.execute(select(Lead).where(Lead.copper_id == lead_data["copper_id"]))
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "copper_id": lead_data["copper_id"]}

    lead = Lead(**lead_data, owner_email=await _owner_for_assignee(db, raw))
    db.add(lead)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return {"status": "duplicate", "copper_id": lead_data["copper_id"]}
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
    request: Request,
    bucket: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    # Repeatable (?source=email_inbox&source=website_form); composes with
    # bucket/status/search (issue #217).
    source: Optional[List[str]] = Query(default=None),
    applied_from: Optional[date] = Query(default=None),
    applied_to: Optional[date] = Query(default=None),
    sort: str = Query(default="newest", pattern="^(newest|oldest)$"),
    page: int = Query(default=1, ge=1),
    # Cap raised to 1000 so the dashboard can load the full pipeline in one page
    # (it groups all leads into YES/MAYBE/REJECT columns; there's no "load more").
    page_size: int = Query(default=20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Lead).options(selectinload(Lead.assessment)).where(
        Lead.owner_email == effective_owner_email(request, user)
    )
    if status:
        query = query.where(Lead.status == status)
    else:
        # Default: hide archived, approved, and awaiting_deck leads from the
        # kanban. Once an email is approved it's queued for sending — nothing
        # left to decide. awaiting_deck leads have no usable context to judge
        # yet — they rejoin the board once a deck is attached and
        # re-assessment scores them (issue #67).
        query = query.where(Lead.status.notin_(["archived", "approved", "awaiting_deck"]))
    if search:
        query = query.where(Lead.company_name.ilike(f"%{search}%"))
    if source:
        query = query.where(Lead.source.in_(source))
    if applied_from:
        query = query.where(Lead.applied_at >= datetime.combine(applied_from, time.min, tzinfo=timezone.utc))
    if applied_to:
        query = query.where(Lead.applied_at <= datetime.combine(applied_to, time.max, tzinfo=timezone.utc))

    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()

    order = Lead.created_at.asc() if sort == "oldest" else Lead.created_at.desc()
    query = query.order_by(order).offset((page - 1) * page_size).limit(page_size)
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


@router.get("/outbox-health")
async def outbox_health(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Admin-only visibility into the copper_outbox drain (issue #65): counts
    of rows by status, plus the most recent `failed` rows -- including
    write-backs that were skipped outright because a required config id
    (e.g. copper_unqualified_status_id) was unset. A missing config id used
    to fail silently via a bare print(); it now lands here as a failed row."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    from app.models.copper_outbox import CopperOutbox

    counts_result = await db.execute(
        select(CopperOutbox.status, func.count()).group_by(CopperOutbox.status)
    )
    counts = {row_status: count for row_status, count in counts_result.all()}

    recent_result = await db.execute(
        select(CopperOutbox)
        .where(CopperOutbox.status == "failed")
        .order_by(CopperOutbox.created_at.desc())
        .limit(limit)
    )
    recent_failed = recent_result.scalars().all()

    return {
        "counts": counts,
        "recent_failed": [
            {
                "endpoint": row.endpoint,
                "copper_id": row.copper_id,
                "method": row.method,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "created_at": row.created_at.isoformat(),
            }
            for row in recent_failed
        ],
    }


@router.get("/orphans")
async def list_orphans(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Admin-only visibility into leads whose Copper record vanished with no
    live twin (same company name / contact email) to explain it away as a
    merge (issue #174). Each row is a `copper_record_vanished` LeadEvent,
    newest first -- the auditable trail for a partner-visible lead
    disappearing, instead of it just silently dropping off the board."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await db.execute(
        select(LeadEvent, Lead)
        .join(Lead, Lead.id == LeadEvent.lead_id)
        .where(LeadEvent.event_type == EVENT_COPPER_RECORD_VANISHED)
        .order_by(LeadEvent.created_at.desc())
        .limit(limit)
    )
    rows = result.all()

    return {
        "count": len(rows),
        "orphans": [
            {
                "lead_id": str(lead.id),
                "company_name": lead.company_name,
                "owner_email": lead.owner_email,
                "copper_id": (event.payload or {}).get("copper_id"),
                "vanished_at": event.created_at.isoformat(),
            }
            for event, lead in rows
        ],
    }


@router.get("/failed-summary")
async def failed_summary(
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Admin-only visibility into leads dead-lettered to 'failed' (issue
    #163): total count, a breakdown by owner, and the most common distinct
    `last_assessment_error` strings -- so a burst like the 2026-09-15
    incident (367 leads stuck failed, root cause only ever print()ed to
    worker logs) can be diagnosed and traced to a single root cause from the
    app/DB instead of grepping container logs."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    total_result = await db.execute(select(func.count()).where(Lead.status == "failed"))
    total = total_result.scalar_one()

    by_owner_result = await db.execute(
        select(Lead.owner_email, func.count())
        .where(Lead.status == "failed")
        .group_by(Lead.owner_email)
    )
    by_owner = {(owner or "unassigned"): count for owner, count in by_owner_result.all()}

    by_error_result = await db.execute(
        select(Lead.last_assessment_error, func.count())
        .where(Lead.status == "failed", Lead.last_assessment_error.is_not(None))
        .group_by(Lead.last_assessment_error)
        .order_by(func.count().desc())
        .limit(limit)
    )
    top_errors = [{"error": error, "count": count} for error, count in by_error_result.all()]

    return {"total": total, "by_owner": by_owner, "top_errors": top_errors}


@router.get("/{lead_id}", response_model=LeadWithAssessment)
async def get_lead(
    lead_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Lead).options(selectinload(Lead.assessment)).where(
            Lead.id == lead_id, Lead.owner_email == effective_owner_email(request, user)
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
    prior_status = lead.status
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
        existing_tags = None
        outbox_id = None
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            outbox_id = copper_writer.archive_in_copper(lead.copper_id, existing_tags)
        except Exception as exc:
            print(f"[delete_lead] Copper write failed (local commit succeeded): {exc!r}")

        # Snapshot so override_bucket can correct a stale Unqualified
        # disposition later (issue #157) -- same lead_action_log mechanism
        # archive_no_reply's undo snapshot uses below.
        from app.services.undo import ACTION_DELETE, record_archive_action
        await record_archive_action(
            db, lead=lead, action_type=ACTION_DELETE,
            prior_status=prior_status, prior_tags=existing_tags,
            actor_email=user.email, copper_outbox_id=outbox_id,
        )
        await db.commit()


@router.get("/archive/list")
async def list_archive(
    request: Request,
    sort: str = Query(default="newest", pattern="^(newest|oldest)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns archived leads grouped by the action that archived them.
    Outcomes: sent_meeting_request, sent_rejection, sent_other, no_reply, manual.
    """
    order = Lead.updated_at.asc() if sort == "oldest" else Lead.updated_at.desc()
    result = await db.execute(
        select(Lead)
        .options(selectinload(Lead.assessment))
        .where(Lead.status == "archived", Lead.owner_email == effective_owner_email(request, user))
        .order_by(order)
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


@router.post("/bulk-archive", response_model=BulkArchiveResult)
async def bulk_archive_leads(
    body: BulkArchiveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Archive many leads in one action. Each lead gets the same treatment as
    the single-lead archive-no-reply path -- status=archived, an `archived`
    LeadEvent (reason=bulk_archive), and the full Copper write-back
    (Unqualified + tags + best-effort AI reason/detail) -- but WITHOUT the
    single-archive rating gate: bulk archive is an explicit bulk disposition,
    and requiring every lead in the batch to already be rated would make
    clearing a batch impractical.

    The Copper write-back (AI reason/detail generation + the archive call
    itself) runs in `bulk_archive_writeback_task`, one Celery task per lead,
    rather than inline here: auto-generating a reason for every selected lead
    means one LLM call per lead, and running N of those synchronously inside
    the request would make a large batch slow to respond. This endpoint only
    does the fast, synchronous part (status + event + commit) and dispatches
    the rest so it returns as soon as statuses are flipped.

    Per-lead isolation: each lead is processed in its own try/except so one
    bad id or a mid-batch DB hiccup never aborts the rest -- it's recorded in
    `failed` and the loop moves on. A DB-level exception poisons the
    session's transaction until rolled back, so the except clause rolls back
    before continuing to the next lead.
    """
    block_if_impersonating(request, user)

    from app.tasks.bulk_archive_writeback import bulk_archive_writeback_task

    archived = 0
    copper_enqueued = 0
    failed: list[dict] = []

    for raw_lead_id in body.lead_ids:
        try:
            try:
                lead_uuid = uuid.UUID(str(raw_lead_id))
            except ValueError:
                failed.append({"lead_id": str(raw_lead_id), "error": "invalid_lead_id"})
                continue

            result = await db.execute(
                select(Lead).where(Lead.id == lead_uuid, Lead.owner_email == user.email)
            )
            lead = result.scalar_one_or_none()
            if not lead:
                failed.append({"lead_id": str(raw_lead_id), "error": "not_found"})
                continue

            if lead.status == "archived":
                archived += 1
                continue

            lead.status = "archived"
            await log_event(db, lead.id, EVENT_ARCHIVED, {"reason": "bulk_archive"})
            await db.commit()
            archived += 1

            if lead.copper_id and not lead.copper_opportunity_id:
                bulk_archive_writeback_task.delay(str(lead.id))
                copper_enqueued += 1
        except Exception as exc:
            await db.rollback()
            failed.append({"lead_id": str(raw_lead_id), "error": repr(exc)})

    return BulkArchiveResult(archived=archived, copper_enqueued=copper_enqueued, failed=failed)


def _normalize_reassign_request(body) -> tuple[str, list[str]]:
    from_owner = body.from_owner.strip().lower()
    to_owners = [email.strip().lower() for email in body.to_owners]
    return from_owner, to_owners


@router.post("/reassign/preview", response_model=BulkReassignPreviewResult)
async def preview_bulk_reassign(
    body: BulkReassignPreviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Admin-only dry run for bulk reassignment (issue #194): shows how many
    of `from_owner`'s leads would move, broken down by status/bucket/target,
    without writing anything. The UI shows this before the operator confirms
    via POST /leads/reassign -- its `count` must equal that call's
    `confirm_count` exactly, so the two must share this same matching logic
    (app.services.bulk_reassign.matching_leads)."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    from_owner, to_owners = _normalize_reassign_request(body)
    leads = await bulk_reassign.matching_leads(
        db, from_owner, buckets=body.resolved_buckets(), include_converted=body.include_converted,
    )
    assignments = bulk_reassign.round_robin_assignments(leads, to_owners)
    breakdown = bulk_reassign.breakdown_counts(leads, assignments, to_owners)

    return BulkReassignPreviewResult(count=len(leads), **breakdown)


@router.post("/reassign", response_model=BulkReassignResult)
async def execute_bulk_reassign(
    body: BulkReassignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Admin-only bulk reassignment (issue #194) -- moves every one of
    `from_owner`'s matched leads to `to_owners`, round-robin, in one action.
    Covers the out-of-office case: today the only paths are editing the
    assignee per lead in Copper, or the single-lead
    POST /duplicates/reassign -- neither usable for a hundred leads.

    Refuses upfront (400, nothing written) if any target lacks a
    copper_user_id: reconcile_ownership_task snaps owner_email back to
    Copper's current assignee every 5 minutes, so a reassignment that can't
    also be pushed to Copper would silently revert within 5 minutes and look
    like data loss. Also refuses (409, nothing written) if `confirm_count`
    doesn't match the current matching-lead count -- a cheap guard against
    the board shifting between preview and execute.

    Per lead: sets owner_email, logs a `reassigned` LeadEvent stamped with
    the shared `batch_id` (so a mistaken run can be identified and reversed
    by reassigning that batch back -- issue explicitly scopes an undo
    endpoint out), and enqueues copper_writer.push_assignee through the
    outbox. Each lead runs in its own try/except with rollback, mirroring
    bulk_archive_leads above, so one failure can't abort the batch.

    Each lead is re-fetched by id inside its own try, rather than reusing the
    `Lead` objects `matching_leads()` already fetched: `Session.rollback()`
    expires every object still in the session regardless of
    `expire_on_commit=False` (that flag only governs `commit()`), so after one
    lead's rollback, touching a stale attribute on the next -- or on the
    `target_users` User objects resolved earlier -- would trigger a
    synchronous refresh and raise MissingGreenlet on an AsyncSession. A fresh
    `select` per lead sidesteps that; `copper_user_id` is snapshotted into a
    plain dict up front for the same reason.
    """
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    block_if_impersonating(request, user)

    from_owner, to_owners = _normalize_reassign_request(body)

    target_users, missing = await bulk_reassign.resolve_targets(db, to_owners)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Target(s) missing a Copper user id, refusing to reassign: {', '.join(missing)}",
        )

    leads = await bulk_reassign.matching_leads(
        db, from_owner, buckets=body.resolved_buckets(), include_converted=body.include_converted,
    )
    if len(leads) != body.confirm_count:
        raise HTTPException(
            status_code=409,
            detail=f"confirm_count ({body.confirm_count}) no longer matches the current count "
                   f"({len(leads)}) -- the board changed since preview. Re-run the preview.",
        )

    assignments = bulk_reassign.round_robin_assignments(leads, to_owners)
    batch_id = uuid.uuid4()

    # Primary-key access on an expired ORM instance is safe (the identity map
    # satisfies it without a reload); anything else isn't -- so only `.id` is
    # read off the pre-loop `leads`/`target_users` objects below.
    lead_ids = [lead.id for lead in leads]
    target_copper_user_ids = {email: u.copper_user_id for email, u in target_users.items()}

    moved = 0
    by_target: dict[str, int] = {}
    failed: list[dict] = []

    for lead_id in lead_ids:
        target_email = assignments[lead_id]
        try:
            result = await db.execute(select(Lead).where(Lead.id == lead_id))
            lead = result.scalar_one_or_none()
            if not lead:
                failed.append({"lead_id": str(lead_id), "error": "not_found"})
                continue

            from_email = lead.owner_email
            lead.owner_email = target_email
            await log_event(
                db, lead.id, EVENT_REASSIGNED,
                {
                    "from_owner": from_email,
                    "to_owner": target_email,
                    "source": "bulk_reassign",
                    "by": user.email,
                    "batch_id": str(batch_id),
                },
            )
            await db.commit()

            if lead.copper_id:
                copper_writer.push_assignee(lead.copper_id, target_copper_user_ids[target_email])

            moved += 1
            by_target[target_email] = by_target.get(target_email, 0) + 1
        except Exception as exc:
            await db.rollback()
            failed.append({"lead_id": str(lead_id), "error": repr(exc)})

    return BulkReassignResult(batch_id=batch_id, moved=moved, by_target=by_target, failed=failed)


@router.post("/bulk-send-rejection/preview", response_model=BulkSendRejectionPreviewResult)
async def preview_bulk_send_rejection(
    body: BulkSendRejectionPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Dry run for POST /leads/bulk-send-rejection (issue #205): the review
    screen's data. For each submitted lead_id, returns the recipient address
    and a draft excerpt the partner is about to commit to sending, plus an
    `eligible` verdict with a `reason` when false -- nothing is sent and
    nothing is written.

    This is the first bulk action in the product that is irreversible and
    outward-facing (a sent email can't be unsent), so unlike bulk-archive/
    bulk-reassign this is gated behind is_owner() in addition to the usual
    per-user lead scoping, and (like every other bulk mutation) refuses
    outright while an admin is viewing another user's board via `view_as`.
    """
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    block_if_impersonating(request, user)

    items: list[BulkSendRejectionPreviewItem] = []
    eligible_count = 0
    # Dedupe (preserving order): a repeated id must count, and resolve, once --
    # not look eligible twice in `eligible_count` only for the send endpoint's
    # own dedupe to then queue a single task per id (see send_bulk_rejection).
    for raw_lead_id in dict.fromkeys(body.lead_ids):
        lead, card, not_found_reason = await bulk_rejection.load_lead_and_card(db, raw_lead_id, user.email)
        if lead is None:
            items.append(BulkSendRejectionPreviewItem(
                lead_id=str(raw_lead_id), eligible=False, reason=not_found_reason,
            ))
            continue

        reason = bulk_rejection.eligibility_reason(lead, card)
        items.append(BulkSendRejectionPreviewItem(
            lead_id=str(lead.id),
            company_name=lead.company_name,
            recipient_email=bulk_rejection.recipient_email(lead),
            draft_subject=card.draft_subject if card else None,
            draft_excerpt=(card.draft_body[:200] if card and card.draft_body else None),
            eligible=reason is None,
            reason=reason,
        ))
        if reason is None:
            eligible_count += 1

    return BulkSendRejectionPreviewResult(eligible_count=eligible_count, items=items)


@router.post("/bulk-send-rejection", response_model=BulkSendRejectionResult)
async def send_bulk_rejection(
    body: BulkSendRejectionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sends the already-drafted rejection email to every eligible lead in
    `lead_ids` -- one separate, individually addressed email per founder,
    never a shared/grouped message (issue #205).

    Eligibility is re-checked here from scratch (never trusted from the
    preview, which may be minutes old), and `confirm_count` must equal the
    freshly-computed eligible count or this refuses with 409 and sends
    nothing -- a cheap guard against the board changing between preview and
    confirm.

    The actual send happens in `send_bulk_rejection_task`, one Celery task per
    lead, spaced `settings.bulk_rejection_send_interval_seconds` apart via an
    increasing `countdown` rather than fired all at once -- so one lead's
    SMTP failure can't abort the rest, and a large batch doesn't risk
    submission@raed.vc's deliverability. Each task is independently idempotent
    on AssessmentCard.sent_at, so retrying or re-running this same batch sends
    nothing further for a lead already sent.
    """
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    block_if_impersonating(request, user)

    eligible: list[tuple[Lead, object]] = []
    skipped: list[BulkSendRejectionSkipped] = []
    # Dedupe (preserving order): an id repeated in the request must resolve
    # once, not insert two "queued" BulkRejectionBatchItem rows for the same
    # (batch_id, lead_id) and dispatch two tasks -- the second task's
    # `_get_item` would then raise MultipleResultsFound and both would fail
    # without ever sending.
    for raw_lead_id in dict.fromkeys(body.lead_ids):
        lead, card, not_found_reason = await bulk_rejection.load_lead_and_card(db, raw_lead_id, user.email)
        if lead is None:
            skipped.append(BulkSendRejectionSkipped(lead_id=str(raw_lead_id), reason=not_found_reason))
            continue
        reason = bulk_rejection.eligibility_reason(lead, card)
        if reason:
            skipped.append(BulkSendRejectionSkipped(lead_id=str(lead.id), reason=reason))
            continue
        eligible.append((lead, card))

    if len(eligible) != body.confirm_count:
        raise HTTPException(
            status_code=409,
            detail=f"confirm_count ({body.confirm_count}) no longer matches the current eligible count "
                   f"({len(eligible)}) -- the board changed since preview. Re-run the preview.",
        )

    from app.tasks.send_bulk_rejection import send_bulk_rejection_task

    batch_id = uuid.uuid4()
    for lead, _card in eligible:
        db.add(BulkRejectionBatchItem(
            batch_id=batch_id, lead_id=lead.id, owner_email=user.email,
            company_name=lead.company_name, status="queued",
        ))
    for s in skipped:
        try:
            skipped_lead_uuid = uuid.UUID(s.lead_id)
        except ValueError:
            continue
        db.add(BulkRejectionBatchItem(
            batch_id=batch_id, lead_id=skipped_lead_uuid, owner_email=user.email,
            company_name=None, status="skipped", reason=s.reason,
        ))
    await db.commit()

    interval = settings.bulk_rejection_send_interval_seconds
    for index, (lead, _card) in enumerate(eligible):
        send_bulk_rejection_task.apply_async(
            args=[str(batch_id), str(lead.id), user.email],
            countdown=index * interval,
        )

    return BulkSendRejectionResult(batch_id=batch_id, queued=len(eligible), skipped=skipped)


@router.get("/bulk-send-rejection/{batch_id}", response_model=BulkSendRejectionBatchStatus)
async def get_bulk_send_rejection_batch(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sent/failed/skipped counts + per-lead reasons for one
    POST /leads/bulk-send-rejection batch (issue #205), so a partial batch is
    auditable rather than guessed at. Scoped to the batch's own owner -- one
    partner can't read another's send results."""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await db.execute(
        select(BulkRejectionBatchItem)
        .where(BulkRejectionBatchItem.batch_id == batch_id, BulkRejectionBatchItem.owner_email == user.email)
        .order_by(BulkRejectionBatchItem.created_at.asc())
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Batch not found")

    counts = {"sent": 0, "failed": 0, "skipped": 0, "queued": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    return BulkSendRejectionBatchStatus(
        batch_id=batch_id,
        sent=counts["sent"], failed=counts["failed"], skipped=counts["skipped"], queued=counts["queued"],
        items=[
            BulkSendRejectionBatchItemOut(
                lead_id=str(row.lead_id), company_name=row.company_name,
                status=row.status, reason=row.reason,
            )
            for row in rows
        ],
    )


def _require_filter_or_ids(body: BulkReassessPreviewRequest) -> None:
    if not body.lead_ids and not body.bucket and not body.assessed_before:
        raise HTTPException(
            status_code=400,
            detail="Provide lead_ids, or a bucket/assessed_before filter, to select leads.",
        )


async def _resolve_bulk_reassess_candidates(db: AsyncSession, owner_email: str, body: BulkReassessPreviewRequest):
    try:
        return await bulk_reassess.resolve_candidates(
            db, owner_email, lead_ids=body.lead_ids, bucket=body.bucket, assessed_before=body.assessed_before,
        )
    except bulk_reassess.NotOwnedError:
        raise HTTPException(status_code=403, detail="Forbidden")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/bulk-reassess/preview", response_model=BulkReassessPreviewResult)
async def preview_bulk_reassess(
    body: BulkReassessPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Dry run for POST /leads/bulk-reassess (issue #203): shows how many of
    the caller's own leads match, how many have changed inputs since their
    last card (the fingerprint app/services/bulk_reassess.py defines --
    company name, description, pitch deck text, and scraped website content
    for deck-less leads), and how many would be skipped as unchanged. Writes
    nothing and queues nothing -- the partner sees "117 match, 12 have new
    information" before deciding whether to run it, and with `force`, before
    confirming every one of them anyway.

    Owner-scoped like the execute endpoint below: block_if_impersonating so
    an admin under `?view_as=` can't preview (or run) someone else's board.
    """
    block_if_impersonating(request, user)
    _require_filter_or_ids(body)

    candidates = await _resolve_bulk_reassess_candidates(db, user.email, body)

    in_flight = sum(1 for lead in candidates if lead.status in bulk_reassess.IN_FLIGHT_STATUSES)
    changed_map = await bulk_reassess.classify(candidates)
    changed = sum(1 for is_changed in changed_map.values() if is_changed)
    matched = len(candidates)
    would_queue = (matched - in_flight) if body.force else changed

    breaker_reason = llm_breaker.open_reason()

    return BulkReassessPreviewResult(
        matched=matched,
        changed=changed,
        would_skip=matched - changed,
        in_flight=in_flight,
        would_queue=would_queue,
        breaker_open=breaker_reason is not None,
        breaker_reason=breaker_reason,
    )


@router.post("/bulk-reassess", response_model=BulkReassessResult)
async def execute_bulk_reassess(
    body: BulkReassessRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Queues a fresh assess_lead_task for many of the caller's own leads at
    once (issue #203) -- built for clearing the MAYBE pile once something
    feeding the prompt has actually changed, rather than re-running a
    temperature=0 assessor that would return the identical bucket.

    Refuses to queue ANYTHING (409) if the LLM breaker is open -- checked
    first, before matching leads or writing anything -- so an account-wide
    DeepSeek outage can't fill the board with parked leads (the 2026-10-04
    incident this issue cites). Also refuses (409) if `confirm_count` no
    longer matches the live count (the board moved since preview), and
    refuses (409) if the batch exceeds `settings.bulk_reassess_batch_cap`.

    Without `force`, only leads whose fingerprint changed since their last
    card are queued; with `force: true`, every matched lead not already
    in-flight is queued regardless. Each lead is processed in its own
    try/except with a fresh re-fetch + rollback-on-failure, mirroring
    execute_bulk_reassign above -- a rollback expires every object still in
    the session, so touching a stale pre-loop Lead after that would raise
    MissingGreenlet.
    """
    block_if_impersonating(request, user)
    _require_filter_or_ids(body)

    breaker_reason = llm_breaker.open_reason()
    if breaker_reason:
        raise HTTPException(
            status_code=409,
            detail=f"DeepSeek is unavailable account-wide, queueing nothing: {breaker_reason}",
        )

    candidates = await _resolve_bulk_reassess_candidates(db, user.email, body)

    cap = settings.bulk_reassess_batch_cap
    if len(candidates) > cap:
        raise HTTPException(
            status_code=409,
            detail=f"Batch of {len(candidates)} leads exceeds the cap of {cap}. "
                   f"Narrow the filter or split into smaller batches.",
        )

    if body.confirm_count != len(candidates):
        raise HTTPException(
            status_code=409,
            detail=f"confirm_count ({body.confirm_count}) no longer matches the current count "
                   f"({len(candidates)}) -- the board changed since preview. Re-run the preview.",
        )

    # Snapshotted before the loop, same reason as execute_bulk_reassign: once
    # any iteration below rolls back, every object still in the session
    # expires, so nothing after this point may read an attribute off a
    # pre-loop `candidates` Lead without triggering a MissingGreenlet.
    candidate_ids = [lead.id for lead in candidates]
    bucket_before_by_id = {lead.id: effective_bucket(lead.assessment) for lead in candidates}
    changed_map = {} if body.force else await bulk_reassess.classify(candidates)

    batch_id = uuid.uuid4()
    queued = 0
    skipped_unchanged = 0
    skipped_in_flight = 0
    failed: list[dict] = []

    for lead_id in candidate_ids:
        try:
            result = await db.execute(select(Lead).where(Lead.id == lead_id))
            lead = result.scalar_one_or_none()
            if not lead:
                failed.append({"lead_id": str(lead_id), "error": "not_found"})
                continue

            if lead.status in bulk_reassess.IN_FLIGHT_STATUSES:
                skipped_in_flight += 1
                continue
            if not body.force and not changed_map.get(lead_id, True):
                skipped_unchanged += 1
                continue

            lead.status = "pending"
            lead.assessment_attempts = 0
            await log_event(
                db, lead.id, EVENT_BULK_REASSESS_QUEUED,
                {"batch_id": str(batch_id), "bucket_before": bucket_before_by_id.get(lead_id)},
            )
            await db.commit()
            assess_lead_task.delay(str(lead.id))
            queued += 1
        except Exception as exc:
            await db.rollback()
            failed.append({"lead_id": str(lead_id), "error": repr(exc)})

    return BulkReassessResult(
        batch_id=batch_id,
        matched=len(candidate_ids),
        queued=queued,
        skipped_unchanged=skipped_unchanged,
        skipped_in_flight=skipped_in_flight,
        failed=failed,
    )


@router.get("/bulk-reassess/{batch_id}", response_model=BulkReassessProgress)
async def bulk_reassess_progress(
    batch_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Progress for a bulk-reassess run (issue #203): queued / assessed /
    still pending / failed, and how many of the finished leads' effective
    bucket actually moved -- the only honest measure of whether the batch
    was worth running. Scoped to the caller's own board (honoring an admin's
    `?view_as=` for QA, like every other scoped read) by joining each
    `bulk_reassess_queued` event to its lead and filtering on owner_email,
    so a batch_id from someone else's run 404s instead of leaking counts.
    """
    owner_email = effective_owner_email(request, user)
    result = await db.execute(
        select(LeadEvent, Lead)
        .join(Lead, LeadEvent.lead_id == Lead.id)
        .options(selectinload(Lead.assessment))
        .where(
            LeadEvent.event_type == EVENT_BULK_REASSESS_QUEUED,
            LeadEvent.payload["batch_id"].astext == str(batch_id),
            Lead.owner_email == owner_email,
        )
    )
    rows = result.all()
    if not rows:
        raise HTTPException(status_code=404, detail="Batch not found")

    return bulk_reassess.summarize_progress(batch_id, rows)


@router.post("/{lead_id}/archive-no-reply")
async def archive_no_reply(
    lead_id: str,
    request: Request,
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
    block_if_impersonating(request, user)
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

    prior_status = lead.status
    lead.status = "archived"
    await log_event(db, lead.id, EVENT_ARCHIVED_NO_REPLY, {})
    await db.commit()

    existing_tags = None
    outbox_id = None
    if lead.copper_id and not lead.copper_opportunity_id:
        # AI-generated reason/detail are additive and best-effort: a failed AI
        # call must never block the archive write below (status=Unqualified).
        reason_option_ids, detail_text = None, None
        if card:
            try:
                unqual = claude_agent.resolve_unqualification_reason(
                    rejection_reasons=getattr(card, "rejection_reasons", None),
                    company_name=lead.company_name,
                    bucket=card.user_override or card.bucket,
                    summary=card.summary,
                    red_flags=card.red_flags,
                    lead_id=str(lead.id),
                )
                reason_option_ids = unqual.get("reason_option_ids")
                detail_text = unqual.get("detail_text")
            except Exception as exc:
                print(f"[archive_no_reply] Unqualification-reason AI call failed (archiving anyway): {exc!r}")
        try:
            existing_tags = (lead.raw_copper_data or {}).get("tags") if lead.raw_copper_data else None
            outbox_id = copper_writer.archive_in_copper(
                lead.copper_id, existing_tags,
                reason_option_ids=reason_option_ids, detail_text=detail_text,
            )
        except Exception as exc:
            print(f"[archive_no_reply] Copper write failed (local commit succeeded): {exc!r}")

    # Snapshot for undo (issue #153) -- prior status + Copper tag set, so
    # POST /leads/{lead_id}/undo can restore exactly what this overwrote.
    from app.services.undo import ACTION_ARCHIVE_NO_REPLY, record_archive_action
    await record_archive_action(
        db, lead=lead, action_type=ACTION_ARCHIVE_NO_REPLY,
        prior_status=prior_status, prior_tags=existing_tags,
        actor_email=user.email, copper_outbox_id=outbox_id,
    )
    await db.commit()

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


@router.post("/{lead_id}/undo")
async def undo_last_action(
    lead_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Reverses the most recent undoable action for this lead -- archive-no-reply,
    the rejection-send archive (issue #153), or the thumbs-down-on-YES
    auto-reject (issue #225; manual override_bucket / approve / bulk-archive
    undo are still deferred follow-ups). For the two archive actions, restores
    app `status` and enqueues a Copper write-back reversing the status + tags
    + clearing the AI-written Unqualification fields. For the auto-reject
    action, restores the assessment card's bucket/override/draft instead and
    mirrors the restored bucket tag back to Copper -- see
    app.services.undo.undo_action.

    Owner-scoped, refused while impersonating. Idempotent: undoing an
    already-undone action returns `already_undone` rather than erroring.
    Refuses (409) if the lead was converted to a Copper Opportunity
    (un-converting is out of scope) or if the lead's status has drifted from
    what the logged action produced -- e.g. it was re-synced or acted on
    again since -- so undo never clobbers newer state.

    A sent email can never be unsent: for a rejection-send archive, undo
    still restores app/Copper state but the response's `email_sent` flag
    (and accompanying `note`) makes clear the email already went out, and
    nothing here re-arms sending (the assessment card's `sent_at` is never
    touched).
    """
    block_if_impersonating(request, user)

    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.owner_email == user.email))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    from app.models.lead_action_log import LeadActionLog
    from app.services import undo as undo_service

    log_result = await db.execute(
        select(LeadActionLog)
        .where(LeadActionLog.lead_id == lead.id)
        .where(LeadActionLog.action_type.in_(list(undo_service.UNDOABLE_ACTIONS)))
        .order_by(LeadActionLog.created_at.desc())
        .limit(1)
    )
    action = log_result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Nothing to undo for this lead")

    if action.undone_at:
        return {"status": "already_undone", "action_type": action.action_type}

    if lead.copper_opportunity_id:
        raise HTTPException(
            status_code=409,
            detail="This lead was converted to a Copper Opportunity — un-converting isn't supported. "
                   "Reverse it manually in Copper if needed.",
        )

    if action.action_type == undo_service.ACTION_BUCKET_OVERRIDE:
        # Different shape of staleness check: this action never touched
        # lead.status, so compare the assessment card's current effective
        # bucket against what the action produced instead (issue #225).
        from app.models.assessment import AssessmentCard
        card_result = await db.execute(
            select(AssessmentCard).where(AssessmentCard.lead_id == lead.id)
            .order_by(AssessmentCard.created_at.desc()).limit(1)
        )
        card = card_result.scalar_one_or_none()
        if not card:
            raise HTTPException(status_code=404, detail="Assessment not found")
        expected_bucket = (action.prior_state or {}).get("new_bucket")
        current_effective = card.user_override or card.bucket
        if current_effective != expected_bucket:
            raise HTTPException(
                status_code=409,
                detail=f"Lead bucket has changed since this action (now '{current_effective}') — "
                       "refusing to undo a stale action.",
            )
        return await undo_service.undo_action(db, lead=lead, action=action, card=card)

    if lead.status != "archived":
        raise HTTPException(
            status_code=409,
            detail=f"Lead status has changed since this action (now '{lead.status}') — "
                   "refusing to undo a stale action.",
        )

    return await undo_service.undo_action(db, lead=lead, action=action)


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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Returns the event timeline for a single lead, oldest first."""
    owns = await db.execute(
        select(Lead.id).where(Lead.id == lead_id, Lead.owner_email == effective_owner_email(request, user))
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
    request: Request,
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

    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.owner_email == effective_owner_email(request, user))
    )
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
