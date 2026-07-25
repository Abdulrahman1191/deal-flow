"""
Tests for the periodic Copper import loop (issue #58): teammates were only
getting a fraction of their Copper-assigned pipeline imported because a single
bad lead record (malformed field, DB constraint, or enqueue failure) raised
unguarded inside sync_one_user's per-lead loop, aborting the rest of that
user's import for the run -- and since nothing had committed yet, the same
partial result repeated on every subsequent run.

Mirrors the queued-result AsyncSession fake used in test_copper_writebacks.py
/ test_sync_pitch_decks.py -- no live DB needed.
"""
import asyncio
from types import SimpleNamespace

from app.tasks import sync_copper as sc


class _FakeResult:
    """Answers either scalar_one_or_none() (existing-lead dedup check) or
    scalars().all() (archive stale-scan), whichever the call site uses."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _FakeSyncSession:
    """Returns queued results for each execute() call in order. commit()/
    rollback() are tracked but otherwise no-ops -- durability ordering is
    asserted via call counts, not a real transaction."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _user(email="teammate@raed.vc", copper_user_id=555):
    # copper_user_id already set -> resolve_copper_id short-circuits without
    # touching the db, so it doesn't consume a queued execute() result.
    return SimpleNamespace(email=email, copper_user_id=copper_user_id)


def _fake_map(bad_ids=()):
    def _map(raw):
        if raw["id"] in bad_ids:
            raise ValueError(f"malformed field on {raw['id']}")
        return {"copper_id": raw["id"], "company_name": raw["id"]}
    return _map


def test_one_bad_lead_does_not_abort_the_rest_of_the_batch(monkeypatch):
    raw_leads = [{"id": "good-1"}, {"id": "bad"}, {"id": "good-2"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map(bad_ids={"bad"}))

    queued = []
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    # 3 dedup checks (one per raw lead, none pre-existing) + 1 archive scan.
    db = _FakeSyncSession([None, None, None, []])
    result = asyncio.run(sc.sync_one_user(db, _user()))

    assert result["synced"] == 2
    assert result["failed"] == 1
    assert result["skipped_existing"] == 0
    assert len(db.added) == 2
    assert queued == [str(lead.id) for lead in db.added]
    # One rollback for the bad lead, one commit per good lead, plus the final
    # archive-step commit.
    assert db.rollbacks == 1
    assert db.commits == 3


def test_enqueue_failure_does_not_lose_the_committed_lead(monkeypatch):
    raw_leads = [{"id": "flaky-broker"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())

    def _boom(lead_id):
        raise RuntimeError("redis unreachable")
    monkeypatch.setattr(sc.assess_lead_task, "delay", _boom)

    db = _FakeSyncSession([None, []])
    result = asyncio.run(sc.sync_one_user(db, _user()))

    # The lead was added and committed before the enqueue was ever attempted,
    # so a broker hiccup must not roll it back or drop it from the count.
    assert result["synced"] == 1
    assert result["failed"] == 0
    assert len(db.added) == 1
    assert db.rollbacks == 0


def test_existing_lead_is_skipped_not_recreated(monkeypatch):
    raw_leads = [{"id": "already-here"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    db = _FakeSyncSession([SimpleNamespace(id="existing-row"), []])
    result = asyncio.run(sc.sync_one_user(db, _user()))

    assert result["synced"] == 0
    assert result["skipped_existing"] == 1
    assert db.added == []


def test_second_run_with_nothing_new_imports_nothing(monkeypatch):
    """A repeat run over the same Copper leads (now all pre-existing) must be
    a no-op -- idempotency after the per-lead-commit fix."""
    raw_leads = [{"id": "a"}, {"id": "b"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    db = _FakeSyncSession([
        SimpleNamespace(id="a-row"),
        SimpleNamespace(id="b-row"),
        [],
    ])
    result = asyncio.run(sc.sync_one_user(db, _user()))

    assert result["synced"] == 0
    assert result["skipped_existing"] == 2
    assert result["failed"] == 0
    assert db.added == []
