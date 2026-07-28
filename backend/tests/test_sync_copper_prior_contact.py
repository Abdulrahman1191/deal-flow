"""
Tests for issue #90's Copper-sync integration: populating a lead's
prior_contact/_count/_last_at via app.tasks.sync_copper.maybe_refresh_prior_contact,
with per-lead best-effort isolation (an activities-fetch failure must never
break the surrounding sync) and a refresh-window cache so we don't hit
Copper's activities API for every lead on every 5-min sync cycle.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.tasks import sync_copper as sc


class _FakeCommitSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _lead(**overrides):
    defaults = dict(
        copper_id="123",
        copper_person_id=None,
        prior_contact=None,
        prior_contact_count=None,
        prior_contact_last_at=None,
        prior_contact_checked_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- _needs_prior_contact_refresh: the caching window -----------------------

def test_needs_refresh_when_never_checked():
    assert sc._needs_prior_contact_refresh(_lead(prior_contact_checked_at=None)) is True


def test_no_refresh_when_recently_checked():
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    assert sc._needs_prior_contact_refresh(_lead(prior_contact_checked_at=recent)) is False


def test_refresh_when_checked_value_is_older_than_window(monkeypatch):
    monkeypatch.setattr(sc.settings, "prior_contact_refresh_days", 7)
    stale = datetime.now(timezone.utc) - timedelta(days=8)
    assert sc._needs_prior_contact_refresh(_lead(prior_contact_checked_at=stale)) is True


# --- maybe_refresh_prior_contact: the three acceptance-criteria scenarios --

def test_maybe_refresh_sets_true_for_pre_application_email(monkeypatch):
    application_date = 1_700_000_000
    activities = [
        {"type": {"category": "system", "id": 48}, "activity_date": application_date},
        {"type": {"category": "user", "id": 637593}, "activity_date": application_date - 86400},
    ]
    monkeypatch.setattr(sc, "fetch_lead_activities", lambda cid, pid: activities)

    lead = _lead()
    db = _FakeCommitSession()
    asyncio.run(sc.maybe_refresh_prior_contact(db, lead, {"date_created": application_date}))

    assert lead.prior_contact is True
    assert lead.prior_contact_count == 1
    assert lead.prior_contact_last_at is not None
    assert lead.prior_contact_checked_at is not None
    assert db.commits == 1


def test_maybe_refresh_sets_false_for_post_application_automated_outreach_only(monkeypatch):
    application_date = 1_700_000_000
    activities = [
        {"type": {"category": "system", "id": 48}, "activity_date": application_date},
        {"type": {"category": "user", "id": 637593}, "activity_date": application_date + 60},
    ]
    monkeypatch.setattr(sc, "fetch_lead_activities", lambda cid, pid: activities)

    lead = _lead()
    db = _FakeCommitSession()
    asyncio.run(sc.maybe_refresh_prior_contact(db, lead, {"date_created": application_date}))

    assert lead.prior_contact is False
    assert lead.prior_contact_count == 0
    assert lead.prior_contact_last_at is None
    assert db.commits == 1


def test_maybe_refresh_activities_fetch_failure_leaves_prior_contact_null(monkeypatch):
    def _boom(copper_id, copper_person_id):
        raise RuntimeError("copper 500")

    monkeypatch.setattr(sc, "fetch_lead_activities", _boom)

    lead = _lead()
    db = _FakeCommitSession()
    # Must not raise -- best-effort, per-lead isolation.
    asyncio.run(sc.maybe_refresh_prior_contact(db, lead, {"date_created": 1_700_000_000}))

    assert lead.prior_contact is None
    assert lead.prior_contact_count is None
    assert lead.prior_contact_last_at is None
    assert db.commits == 0


def test_maybe_refresh_skips_when_recently_checked(monkeypatch):
    calls = []
    monkeypatch.setattr(sc, "fetch_lead_activities", lambda cid, pid: calls.append(1))

    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    lead = _lead(prior_contact_checked_at=recent)
    db = _FakeCommitSession()
    asyncio.run(sc.maybe_refresh_prior_contact(db, lead, {"date_created": 1_700_000_000}))

    assert calls == []
    assert db.commits == 0


def test_maybe_refresh_skips_lead_without_copper_id(monkeypatch):
    calls = []
    monkeypatch.setattr(sc, "fetch_lead_activities", lambda cid, pid: calls.append(1))

    lead = _lead(copper_id=None)
    db = _FakeCommitSession()
    asyncio.run(sc.maybe_refresh_prior_contact(db, lead, {"date_created": 1_700_000_000}))

    assert calls == []
    assert db.commits == 0


# --- sync_one_user integration: a fetch failure must not break the import --

class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _Session:
    def __init__(self, results):
        self._results = list(results)
        self.added = []

    async def execute(self, _query):
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def test_sync_one_user_activities_fetch_failure_does_not_break_import(monkeypatch):
    """Acceptance criterion: an activities-fetch failure -> the lead still
    syncs, prior_contact is left null."""
    raw_leads = [{"id": "co-1", "date_created": 1_700_000_000}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(
        sc, "map_copper_lead",
        lambda raw: {"copper_id": raw["id"], "company_name": raw["id"]},
    )
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    def _boom(copper_id, copper_person_id):
        raise RuntimeError("copper 500")

    monkeypatch.setattr(sc, "fetch_lead_activities", _boom)

    db = _Session([None, []])  # no existing lead for co-1, then an empty archive scan
    user = SimpleNamespace(email="teammate@raed.vc", copper_user_id=555)

    result = asyncio.run(sc.sync_one_user(db, user))

    assert result["synced"] == 1
    assert result["failed"] == 0
    lead = db.added[0]
    assert lead.prior_contact is None
    assert lead.prior_contact_count is None
