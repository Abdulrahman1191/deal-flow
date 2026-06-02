from __future__ import annotations
"""
Outbound writes back to Copper CRM.

All writes (except convert_lead_to_opportunity which needs the returned IDs synchronously)
go through the copper_outbox table. A drain worker retries failed rows with exponential
backoff, so a momentary Copper outage or rate-limit doesn't silently drop data.
"""
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings
from app.services.copper_echo_guard import register_outbound_write
from app.services.copper_service import COPPER_BASE, _headers

RAED_RESERVED_TAGS = {"raed:override", "raed:approved", "raed:sent", "raed:archived"}

MAX_ATTEMPTS = 5
# Backoff: 30s, 60s, 120s, 240s, 480s
_BACKOFF_SECONDS = [30, 60, 120, 240, 480]


def _strip_raed_state_tags(tags: Optional[list]) -> list:
    out = []
    for t in tags or []:
        ts = str(t)
        if ts.startswith("raed:bucket:"):
            continue
        if ts in RAED_RESERVED_TAGS:
            continue
        out.append(ts)
    return out


def _enqueue(copper_id: str, endpoint: str, body: dict, method: str = "PUT") -> None:
    """Insert a pending outbox row. Uses a sync session since callers are often sync."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models.copper_outbox import CopperOutbox

    # Derive a sync URL from the async one (replace asyncpg driver with psycopg2).
    sync_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url, pool_pre_ping=True)
    with Session(engine) as session:
        row = CopperOutbox(
            copper_id=copper_id,
            endpoint=endpoint,
            method=method,
            body_json=body,
        )
        session.add(row)
        session.commit()
    engine.dispose()
    register_outbound_write(copper_id, body)


def execute_copper_request(endpoint: str, method: str, body: dict) -> Optional[dict]:
    """Make a single Copper API call. Called by the drain worker."""
    try:
        with httpx.Client(timeout=15) as client:
            r = client.request(
                method,
                f"{COPPER_BASE}{endpoint}",
                headers=_headers(),
                json=body,
            )
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise exc


def set_bucket_tag(copper_id: str, new_bucket: str, existing_tags: Optional[list]) -> None:
    if not copper_id:
        return
    base = _strip_raed_state_tags(existing_tags)
    new_tags = base + [f"raed:bucket:{new_bucket.lower()}", "raed:override"]
    _enqueue(copper_id, f"/leads/{copper_id}", {"tags": new_tags})


def push_assessment(copper_id: str, bucket: str, existing_tags: Optional[list]) -> None:
    """Called after AI assessment completes — pushes bucket tag to Copper."""
    if not copper_id:
        return
    base = _strip_raed_state_tags(existing_tags)
    new_tags = base + [f"raed:bucket:{bucket.lower()}"]
    _enqueue(copper_id, f"/leads/{copper_id}", {"tags": new_tags})


def push_draft_edit(copper_id: str, draft_subject: Optional[str], draft_body: Optional[str]) -> None:
    """F8 — sync analyst-edited draft text to Copper custom fields.
    Skips silently when the custom field IDs aren't configured (one-time setup
    per COPPER_BIDIRECTIONAL_SYNC.md §3)."""
    if not copper_id:
        return
    custom_fields = []
    if settings.copper_cf_draft_subject_id and draft_subject is not None:
        custom_fields.append({
            "custom_field_definition_id": settings.copper_cf_draft_subject_id,
            "value": draft_subject,
        })
    if settings.copper_cf_draft_body_id and draft_body is not None:
        custom_fields.append({
            "custom_field_definition_id": settings.copper_cf_draft_body_id,
            "value": draft_body,
        })
    if not custom_fields:
        print(f"[copper_writer] push_draft_edit: no custom fields configured, skipping copper_id={copper_id}")
        return
    _enqueue(copper_id, f"/leads/{copper_id}", {"custom_fields": custom_fields})


def mark_approved_in_copper(copper_id: str, existing_tags: Optional[list]) -> None:
    """F9 — sync analyst approval to Copper. Adds the `raed:approved` tag and,
    if the `Raed App Status` custom field is configured, sets it to 'approved'."""
    if not copper_id:
        return
    base = _strip_raed_state_tags(existing_tags)
    new_tags = base + ["raed:approved"]
    payload: dict = {"tags": new_tags}
    if settings.copper_cf_app_status_id:
        payload["custom_fields"] = [{
            "custom_field_definition_id": settings.copper_cf_app_status_id,
            "value": "approved",
        }]
    _enqueue(copper_id, f"/leads/{copper_id}", payload)


def mark_sent_in_copper(copper_id: str, existing_tags: Optional[list]) -> None:
    if not copper_id:
        return
    base = _strip_raed_state_tags(existing_tags)
    new_tags = base + ["raed:sent"]
    payload = {
        "tags": new_tags,
        "date_last_contacted": int(datetime.now(timezone.utc).timestamp()),
    }
    _enqueue(copper_id, f"/leads/{copper_id}", payload)


def archive_in_copper(copper_id: str, existing_tags: Optional[list]) -> None:
    if not copper_id:
        return
    if not settings.copper_unqualified_status_id:
        print("[copper_writer] copper_unqualified_status_id is 0; skipping archive write")
        return
    base = _strip_raed_state_tags(existing_tags)
    new_tags = base + ["raed:archived"]
    payload = {"tags": new_tags, "status_id": settings.copper_unqualified_status_id}
    _enqueue(copper_id, f"/leads/{copper_id}", payload)


def reject_in_copper(copper_id: str, existing_tags: Optional[list]) -> None:
    if not copper_id:
        return
    if not settings.copper_unqualified_status_id:
        print("[copper_writer] copper_unqualified_status_id is 0; skipping reject write")
        return
    base = _strip_raed_state_tags(existing_tags)
    new_tags = base + ["raed:bucket:reject", "raed:override", "raed:archived"]
    payload = {"tags": new_tags, "status_id": settings.copper_unqualified_status_id}
    _enqueue(copper_id, f"/leads/{copper_id}", payload)


def convert_lead_to_opportunity(
    copper_id: str,
    company_name: str,
    founder_name: Optional[str],
) -> Optional[dict]:
    """
    Kept synchronous because we need the returned opportunity_id immediately
    to store on the lead row. Not routed through the outbox.
    """
    if not copper_id:
        return None
    if not settings.copper_pipeline_id or not settings.copper_pipeline_stage_id:
        print("[copper_writer] pipeline_id or stage_id is 0; cannot convert lead")
        return None

    payload = {
        "details": {
            "person": {"name": founder_name or company_name},
            "company": {"name": company_name},
            "opportunity": {
                "name": company_name,
                "pipeline_id": settings.copper_pipeline_id,
                "pipeline_stage_id": settings.copper_pipeline_stage_id,
            },
        }
    }
    register_outbound_write(copper_id, payload)
    try:
        with httpx.Client(timeout=20) as client:
            r = client.post(
                f"{COPPER_BASE}/leads/{copper_id}/convert",
                headers=_headers(),
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return {
                "person_id": str((data.get("person") or {}).get("id", "")) or None,
                "company_id": str((data.get("company") or {}).get("id", "")) or None,
                "opportunity_id": str((data.get("opportunity") or {}).get("id", "")) or None,
            }
    except Exception as exc:
        print(f"[copper_writer] convert /leads/{copper_id} failed: {exc!r}")
        return None
