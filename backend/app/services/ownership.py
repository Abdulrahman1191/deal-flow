from __future__ import annotations
"""
Shared Copper-vs-DealFlow ownership reconciliation logic.

Copper is the source of truth for a lead's owner (its `assignee_id`). This
module compares each DealFlow lead's `owner_email` against its *current*
Copper assignee (resolved to a @raed.vc email via `users.copper_user_id`) and
reports/corrects drift. Firm-wide and status-agnostic -- unlike
`sync_copper.py::sync_one_user`, which only reconciles a lead when Copper
currently reports it as *open*-status-assigned to a user this pass is
actively syncing (see issue #123).

Used by:
  - scripts/reconcile_ownership.py -- the human-run, one-off report/--fix CLI
    (issue #114).
  - app/tasks/reconcile_ownership.py -- the periodic beat task that applies
    this automatically, with LeadEvent logging (issue #123).
"""
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.models.lead import Lead
from app.models.user import User
from app.services.events import EVENT_REASSIGNED, log_event

UNASSIGNED = "unassigned"


# --- Data shapes passed into the pure classifier helpers ---------------------

@dataclass
class AppLead:
    id: str
    company_name: str
    copper_id: str
    owner_email: Optional[str]


@dataclass
class Mismatch:
    lead_id: str
    company_name: str
    copper_id: str
    dealflow_owner: str
    copper_assignee: str

    @property
    def fixable(self) -> bool:
        """True if copper_assignee resolved to a known @raed.vc user -- i.e.
        there's a concrete value a fix could write. unassigned/unknown(<id>)
        leads are always skipped; there's nothing known to fix them to."""
        return self.copper_assignee not in (UNASSIGNED,) and not self.copper_assignee.startswith("unknown(")


# --- Classifier helpers (pure, unit-tested, no DB/HTTP access) --------------

def resolve_assignee_email(assignee_id, copper_user_map: dict) -> str:
    """Maps a Copper lead's assignee_id to a @raed.vc email via
    copper_user_id -> email. Falls back to a clear unassigned/unknown(<id>)
    label rather than silently dropping the lead from the report."""
    if not assignee_id:
        return UNASSIGNED
    try:
        aid = int(assignee_id)
    except (TypeError, ValueError):
        return f"unknown({assignee_id})"
    email = copper_user_map.get(aid)
    return email if email else f"unknown({aid})"


def find_mismatches(app_leads: list, copper_index: dict, copper_user_map: dict) -> list:
    """app_leads: AppLead rows with a copper_id. copper_index: copper_id (str)
    -> raw Copper lead dict, from a firm-wide fetch. copper_user_map:
    copper_user_id (int) -> @raed.vc email, from the users table.

    Returns one Mismatch per lead whose resolved current Copper assignee
    differs from Lead.owner_email. Leads whose copper_id isn't present in
    copper_index (e.g. deleted in Copper) are skipped -- there's no current
    assignee to compare against.
    """
    rows: list = []
    for lead in app_leads:
        raw = copper_index.get(lead.copper_id)
        if raw is None:
            continue
        copper_owner = resolve_assignee_email(raw.get("assignee_id"), copper_user_map)
        dealflow_owner = lead.owner_email or UNASSIGNED
        if dealflow_owner.strip().lower() == copper_owner.strip().lower():
            continue
        rows.append(Mismatch(
            lead_id=lead.id,
            company_name=lead.company_name,
            copper_id=lead.copper_id,
            dealflow_owner=dealflow_owner,
            copper_assignee=copper_owner,
        ))
    return rows


def filter_by_owner(rows: list, owner_email: str) -> list:
    """Scopes mismatches to ones involving `owner_email` on either side --
    covers both a lead drifting away from them and one that should be theirs."""
    target = owner_email.strip().lower()
    return [
        r for r in rows
        if r.dealflow_owner.strip().lower() == target or r.copper_assignee.strip().lower() == target
    ]


def filter_by_pair(rows: list, pair: tuple) -> list:
    """Scopes mismatches to ones crossing between exactly these two emails,
    in either direction -- the abdulrahman <-> uday convenience case."""
    a, b = (p.strip().lower() for p in pair)
    wanted = {a, b}
    return [
        r for r in rows
        if r.dealflow_owner.strip().lower() in wanted and r.copper_assignee.strip().lower() in wanted
    ]


def group_counts(rows: list) -> Counter:
    """Per-pair counts: (dealflow_owner, copper_assignee) -> count, so the
    report shows at a glance which reassignments account for the drift."""
    return Counter((r.dealflow_owner, r.copper_assignee) for r in rows)


def sort_rows(rows: list) -> list:
    return sorted(rows, key=lambda r: (r.dealflow_owner, r.copper_assignee, r.company_name))


# --- DB access ----------------------------------------------------------------

async def fetch_app_leads(db) -> list:
    """Firm-wide: every lead with a copper_id, any owner, any status. Filtering
    afterwards (filter_by_owner/filter_by_pair) lets a caller match on either
    side of a mismatch (a query filtered to Lead.owner_email == X would miss
    drift *into* X from another owner)."""
    result = await db.execute(select(Lead).where(Lead.copper_id.is_not(None)))
    leads = result.scalars().all()
    return [
        AppLead(id=str(l.id), company_name=l.company_name, copper_id=l.copper_id, owner_email=l.owner_email)
        for l in leads
    ]


async def fetch_copper_user_map(db) -> dict:
    result = await db.execute(select(User.copper_user_id, User.email).where(User.copper_user_id.is_not(None)))
    return {cid: email for cid, email in result.all()}


async def apply_fix(db, mismatches: list, log_events: bool = False) -> list:
    """Sets owner_email = copper_assignee for every fixable mismatch. Skips
    unfixable ones (unassigned/unknown Copper assignee). Idempotent: rerunning
    against already-fixed leads finds no mismatches at all, since
    find_mismatches only reports leads where the two values still differ.

    log_events=True (the periodic reconcile task, issue #123) also appends a
    `reassigned` LeadEvent per correction with the from/to owner. The manual
    --fix CLI (issue #114) leaves this off -- it's a human-run, one-off
    correction, not the automatic mechanism this event log is meant to track.
    """
    fixed: list = []
    lead_ids = [m.lead_id for m in mismatches if m.fixable]
    if not lead_ids:
        return fixed

    result = await db.execute(select(Lead).where(Lead.id.in_(uuid.UUID(i) for i in lead_ids)))
    leads_by_id = {str(l.id): l for l in result.scalars().all()}

    for m in mismatches:
        if not m.fixable:
            continue
        lead = leads_by_id.get(m.lead_id)
        if lead is None:
            continue
        from_owner = lead.owner_email
        lead.owner_email = m.copper_assignee
        if log_events:
            await log_event(db, lead.id, EVENT_REASSIGNED,
                             {"from_owner": from_owner, "to_owner": m.copper_assignee})
        fixed.append(m)

    if fixed:
        await db.commit()
    return fixed
