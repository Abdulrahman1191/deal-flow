"""
Tests for the periodic ownership-reconcile beat task (issue #123):
app/tasks/reconcile_ownership.py + the log_events=True path of
app/services/ownership.py::apply_fix.

Unlike sync_copper.py::sync_one_user's reassignment (which only reconciles a
lead when Copper reports it as *open*-status-assigned to a user that pass is
actively syncing), this task is firm-wide and status-agnostic: it compares
every Dealflow lead with a copper_id against Copper's current assignee_id and
corrects owner_email on any drift, regardless of the lead's status or which
user (if any) is being synced.

Mirrors the queued-result AsyncSession fake used in test_sync_copper.py /
test_reconcile_ownership.py -- no live DB, no live Copper.
"""
from __future__ import annotations
import asyncio
import uuid
from types import SimpleNamespace

from app.models.event import LeadEvent
from app.services import ownership as ow
from app.tasks import reconcile_ownership as rot

ABDULRAHMAN_ID = 1181364
UDAY_ID = 1150537
USER_MAP = {ABDULRAHMAN_ID: "abdulrahman@raed.vc", UDAY_ID: "uday@raed.vc"}


def copper_lead(cid, assignee_id):
    return {"id": cid, "assignee_id": assignee_id}


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def _db_lead(lead_id, owner_email, copper_id, company_name="Acme", status="pending"):
    return SimpleNamespace(id=lead_id, company_name=company_name, owner_email=owner_email,
                            copper_id=copper_id, status=status)


# --- apply_fix(log_events=True): the automatic-task path ----------------------

def test_apply_fix_logs_reassigned_event_with_from_and_to_owner():
    """Scenario 1: a lead owned by A whose Copper assignee_id is now B ends up
    owner_email=B, with a `reassigned` LeadEvent recording the change."""
    lead_id = uuid.uuid4()
    db_lead = _db_lead(lead_id, "abdulrahman@raed.vc", "100")
    mismatch = ow.Mismatch(str(lead_id), "Acme", "100", "abdulrahman@raed.vc", "uday@raed.vc")
    session = _FakeSession([[db_lead]])

    fixed = asyncio.run(ow.apply_fix(session, [mismatch], log_events=True))

    assert fixed == [mismatch]
    assert db_lead.owner_email == "uday@raed.vc"
    assert session.committed == 1
    assert len(session.added) == 1
    event = session.added[0]
    assert isinstance(event, LeadEvent)
    assert event.event_type == "reassigned"
    assert event.payload == {"from_owner": "abdulrahman@raed.vc", "to_owner": "uday@raed.vc"}
    assert event.lead_id == lead_id


def test_apply_fix_without_log_events_does_not_touch_events():
    """The manual --fix CLI path (issue #114) stays event-free -- default
    log_events=False, so db.add() (undefined on this fake) is never called."""
    lead_id = uuid.uuid4()
    db_lead = _db_lead(lead_id, "abdulrahman@raed.vc", "100")
    mismatch = ow.Mismatch(str(lead_id), "Acme", "100", "abdulrahman@raed.vc", "uday@raed.vc")
    session = _FakeSession([[db_lead]])

    fixed = asyncio.run(ow.apply_fix(session, [mismatch]))

    assert fixed == [mismatch]
    assert db_lead.owner_email == "uday@raed.vc"
    assert session.added == []


def test_apply_fix_skips_unresolvable_assignee_and_leaves_lead_unchanged():
    """Scenario 3: a lead whose Copper assignee maps to no known Dealflow
    user is left unchanged -- no write, no event."""
    mismatch = ow.Mismatch(str(uuid.uuid4()), "Delta", "400", "abdulrahman@raed.vc", "unknown(777777)")
    session = _FakeSession([])

    fixed = asyncio.run(ow.apply_fix(session, [mismatch], log_events=True))

    assert fixed == []
    assert session.committed == 0
    assert session.added == []


def test_apply_fix_skips_unassigned_in_copper():
    mismatch = ow.Mismatch(str(uuid.uuid4()), "Gamma", "300", "abdulrahman@raed.vc", "unassigned")
    session = _FakeSession([])

    fixed = asyncio.run(ow.apply_fix(session, [mismatch], log_events=True))

    assert fixed == []
    assert session.added == []


def test_apply_fix_already_matching_is_untouched():
    """Firm-wide idempotency: find_mismatches never reports a lead whose
    owner_email already matches Copper, so apply_fix has nothing to do."""
    lead = ow.AppLead(id="1", company_name="Acme", copper_id="100", owner_email="uday@raed.vc")
    copper_index = {"100": copper_lead("100", UDAY_ID)}
    mismatches = ow.find_mismatches([lead], copper_index, USER_MAP)
    assert mismatches == []


# --- find_mismatches: status-agnostic (the gap this issue closes) -------------

def test_find_mismatches_reports_drift_regardless_of_lead_status():
    """Scenario 2: a non-open-status lead (e.g. archived/converted) whose
    Copper assignee changed is still reported as a mismatch -- find_mismatches
    (and fetch_app_leads, which it's fed from) never filters on status, unlike
    sync_one_user's fetch_open_leads_for_user."""
    lead = ow.AppLead(id="1", company_name="KMPlus Consulting", copper_id="100",
                       owner_email="abdulrahman@raed.vc")
    copper_index = {"100": copper_lead("100", UDAY_ID)}

    rows = ow.find_mismatches([lead], copper_index, USER_MAP)

    assert len(rows) == 1
    assert rows[0].dealflow_owner == "abdulrahman@raed.vc"
    assert rows[0].copper_assignee == "uday@raed.vc"


# --- reconcile_ownership_task._run(): end-to-end wiring ------------------------

def test_run_fixes_non_open_status_lead_and_logs_event(monkeypatch):
    """End-to-end: a non-open-status lead with drifted ownership gets
    corrected and logged by a single task run, with no live Copper/DB."""
    lead_id = uuid.uuid4()
    db_lead = _db_lead(lead_id, "abdulrahman@raed.vc", "100", "KMPlus Consulting", status="archived")

    monkeypatch.setattr(rot, "fetch_all_leads", lambda: [copper_lead("100", UDAY_ID)])

    # fetch_app_leads -> execute() #1, fetch_copper_user_map -> execute() #2,
    # apply_fix -> execute() #3 (re-fetch fixable leads by id).
    session = _FakeSession([[db_lead], list(USER_MAP.items()), [db_lead]])

    class _CtxSession:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(rot, "CelerySessionLocal", lambda: _CtxSession())

    result = asyncio.run(rot._run())

    assert result == {"mismatches": 1, "fixed": 1, "unresolved": 0}
    assert db_lead.owner_email == "uday@raed.vc"
    assert db_lead.status == "archived"  # reconcile only touches ownership, not status
    assert len(session.added) == 1
    assert session.added[0].event_type == "reassigned"
    assert session.added[0].payload == {"from_owner": "abdulrahman@raed.vc", "to_owner": "uday@raed.vc"}


def test_run_leaves_unresolvable_assignee_unchanged(monkeypatch):
    lead_id = uuid.uuid4()
    db_lead = _db_lead(lead_id, "abdulrahman@raed.vc", "400", "Delta", status="pending")

    monkeypatch.setattr(rot, "fetch_all_leads", lambda: [copper_lead("400", 777777)])

    session = _FakeSession([[db_lead], list(USER_MAP.items())])

    class _CtxSession:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(rot, "CelerySessionLocal", lambda: _CtxSession())

    result = asyncio.run(rot._run())

    assert result == {"mismatches": 1, "fixed": 0, "unresolved": 1}
    assert db_lead.owner_email == "abdulrahman@raed.vc"
    assert session.added == []
    assert session.committed == 0
