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

import pytest

from app.models.event import LeadEvent
from app.models.lead import Lead
from app.tasks import sync_copper as sc


@pytest.fixture(autouse=True)
def _stub_prior_contact_refresh(monkeypatch):
    """This file's tests exercise the import/reconcile loop itself. Prior-
    contact refresh (issue #90) is covered separately in
    test_sync_copper_prior_contact.py -- stub it to a no-op here so it
    doesn't perturb these tests' commit-count assertions or (for tests that
    build a real Lead() row) attempt a live Copper network call."""
    async def _noop(_db, _lead, _raw):
        return None

    monkeypatch.setattr(sc, "maybe_refresh_prior_contact", _noop)


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


def _fake_map(bad_ids=(), pitch_deck_text=None):
    def _map(raw):
        if raw["id"] in bad_ids:
            raise ValueError(f"malformed field on {raw['id']}")
        mapped = {"copper_id": raw["id"], "company_name": raw["id"]}
        if pitch_deck_text is not None:
            mapped["pitch_deck_text"] = pitch_deck_text
        return mapped
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
    new_leads = [obj for obj in db.added if isinstance(obj, Lead)]
    assert len(new_leads) == 2
    # Deck-less brand-new leads (issue #149) are parked in awaiting_deck
    # instead of assessed immediately, so nothing gets queued here.
    assert queued == []
    assert all(lead.status == "awaiting_deck" for lead in new_leads)
    assert all(lead.deck_wait_started_at is not None for lead in new_leads)
    # One rollback for the bad lead; each good lead now commits twice (create
    # + awaiting_deck park), plus the final archive-step commit.
    assert db.rollbacks == 1
    assert db.commits == 5


def test_new_deckless_lead_is_parked_awaiting_deck_not_assessed_immediately(monkeypatch):
    """Issue #149, acceptance criterion 1: a brand-new lead with no
    pitch_deck_text must land in awaiting_deck on import rather than being
    website-assessed right away -- giving a deck that's about to be uploaded
    a chance to be used instead of a premature deck-less verdict."""
    raw_leads = [{"id": "no-deck-co"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())

    queued = []
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    db = _FakeSyncSession([None, []])
    result = asyncio.run(sc.sync_one_user(db, _user()))

    assert result["synced"] == 1
    lead = next(obj for obj in db.added if isinstance(obj, Lead))
    assert lead.status == "awaiting_deck"
    assert lead.deck_wait_started_at is not None
    assert queued == []

    events = [obj for obj in db.added if isinstance(obj, LeadEvent)]
    assert len(events) == 1
    assert events[0].event_type == "awaiting_deck"


def test_new_lead_with_deck_already_present_is_assessed_immediately(monkeypatch):
    """Contrast case: a lead that already carries deck text at import time
    (e.g. mapped from a Copper field) is assessed right away like before --
    the awaiting_deck park only applies to genuinely deck-less new leads."""
    raw_leads = [{"id": "has-deck-co"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map(pitch_deck_text="deck contents"))

    queued = []
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    db = _FakeSyncSession([None, []])
    result = asyncio.run(sc.sync_one_user(db, _user()))

    assert result["synced"] == 1
    lead = next(obj for obj in db.added if isinstance(obj, Lead))
    assert lead.status != "awaiting_deck"
    assert queued == [str(lead.id)]


def test_enqueue_failure_does_not_lose_the_committed_lead(monkeypatch):
    """A lead that already carries deck text (unusual for a brand-new Copper
    import, but exercises the enqueue path deterministically) takes the
    immediate-assessment branch rather than parking in awaiting_deck, so a
    broker hiccup on the enqueue is the thing under test here."""
    raw_leads = [{"id": "flaky-broker"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map(pitch_deck_text="already have a deck"))

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


def test_commit_failure_on_new_lead_counts_as_failed_only(monkeypatch):
    """If db.commit() itself raises for a new lead (e.g. a DB constraint error
    -- the exact failure class the #58 per-lead isolation guards against),
    that lead must count only as `failed`, not also as `synced`. new_count
    must only increment after the commit that creates the row has actually
    succeeded -- the earlier bad-lead test doesn't catch this because that
    failure fires in map_copper_lead, before new_count is ever touched."""
    raw_leads = [{"id": "will-fail-commit"}, {"id": "good"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    class _FailFirstCommitSession(_FakeSyncSession):
        def __init__(self, results):
            super().__init__(results)
            self._commit_calls = 0

        async def commit(self):
            self._commit_calls += 1
            if self._commit_calls == 1:
                raise RuntimeError("db constraint violation")
            self.commits += 1

    # 2 dedup checks (neither pre-existing) + 1 archive scan.
    db = _FailFirstCommitSession([None, None, []])
    result = asyncio.run(sc.sync_one_user(db, _user()))

    assert result["synced"] == 1
    assert result["failed"] == 1
    assert db.rollbacks == 1


def test_existing_lead_is_skipped_not_recreated(monkeypatch):
    """A copper_id already owned by this same user is left as-is -- in
    particular, not reactivated (see the reassignment tests below for the
    different-owner case)."""
    raw_leads = [{"id": "already-here"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    user = _user()
    db = _FakeSyncSession([SimpleNamespace(id="existing-row", owner_email=user.email), []])
    result = asyncio.run(sc.sync_one_user(db, user))

    assert result["synced"] == 0
    assert result["reassigned"] == 0
    assert result["skipped_existing"] == 1
    assert db.added == []


def test_second_run_with_nothing_new_imports_nothing(monkeypatch):
    """A repeat run over the same Copper leads (now all pre-existing, owned by
    this same user) must be a no-op -- idempotency after the per-lead-commit
    fix."""
    raw_leads = [{"id": "a"}, {"id": "b"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    user = _user()
    db = _FakeSyncSession([
        SimpleNamespace(id="a-row", owner_email=user.email),
        SimpleNamespace(id="b-row", owner_email=user.email),
        [],
    ])
    result = asyncio.run(sc.sync_one_user(db, user))

    assert result["synced"] == 0
    assert result["reassigned"] == 0
    assert result["skipped_existing"] == 2
    assert result["failed"] == 0
    assert db.added == []


def test_existing_lead_owned_by_different_user_is_reassigned(monkeypatch):
    """Issue #61: the whole firm's pipeline was originally imported under one
    owner. When Copper now shows a lead open-assigned to a different
    teammate, the existing row must be reassigned (UPDATE), not skipped
    forever and not duplicated (copper_id has a global unique index)."""
    raw_leads = [{"id": "co-1", "company_name": "Fresh Co Name"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    queued = []
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: queued.append(lead_id))

    lead_id = "11111111-1111-1111-1111-111111111111"
    existing_lead = SimpleNamespace(
        id=lead_id, owner_email="old-owner@raed.vc", copper_id="co-1",
        company_name="Stale Name", status="archived",
    )
    new_owner = _user(email="new-owner@raed.vc")
    db = _FakeSyncSession([existing_lead, []])
    result = asyncio.run(sc.sync_one_user(db, new_owner))

    assert result["synced"] == 0
    assert result["reassigned"] == 1
    assert result["skipped_existing"] == 0
    assert result["failed"] == 0
    assert existing_lead.owner_email == "new-owner@raed.vc"
    assert existing_lead.status == "pending"
    assert existing_lead.company_name == "co-1"  # refreshed via map_copper_lead
    # A LeadEvent recording the reassignment was logged.
    assert len(db.added) == 1
    event = db.added[0]
    assert isinstance(event, LeadEvent)
    assert event.event_type == "reassigned"
    assert event.payload == {"from_owner": "old-owner@raed.vc", "to_owner": "new-owner@raed.vc"}
    # Re-assessed so it lands on the new owner's board.
    assert queued == [lead_id]


def test_reassigned_lead_is_not_reassigned_again_on_next_sync(monkeypatch):
    """Idempotency: once reassigned, the next sync sees the row already owned
    by this user and takes the same-owner skip path -- no repeated
    reassignment, no duplicate assessments every cycle."""
    raw_leads = [{"id": "co-1"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    user = _user(email="new-owner@raed.vc")
    # Second run: the row is now owned by this same user (post-reassignment).
    already_reassigned = SimpleNamespace(id="lead-1", owner_email=user.email, copper_id="co-1")
    db = _FakeSyncSession([already_reassigned, []])
    result = asyncio.run(sc.sync_one_user(db, user))

    assert result["reassigned"] == 0
    assert result["skipped_existing"] == 1
    assert db.added == []


def test_reassigned_lead_is_not_re_archived_by_stale_reconcile(monkeypatch):
    """A lead just reassigned to this user in this same run is open-assigned
    to them in Copper (its copper_id is in this run's fetch), so the
    stale-archive reconcile scan right after must not archive it again."""
    raw_leads = [{"id": "co-1"}]
    monkeypatch.setattr(sc, "fetch_open_leads_for_user", lambda cid: raw_leads)
    monkeypatch.setattr(sc, "map_copper_lead", _fake_map())
    monkeypatch.setattr(sc.assess_lead_task, "delay", lambda lead_id: None)

    user = _user(email="new-owner@raed.vc")
    existing_lead = SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        owner_email="old-owner@raed.vc", copper_id="co-1", status="archived",
    )
    # Archive-reconcile scan (2nd execute()) returns the same row, now
    # reassigned to `user` and still open (copper_id "co-1" is in copper_ids).
    db = _FakeSyncSession([existing_lead, [existing_lead]])
    result = asyncio.run(sc.sync_one_user(db, user))

    assert result["reassigned"] == 1
    assert result["archived_stale"] == 0
    assert existing_lead.status == "pending"
