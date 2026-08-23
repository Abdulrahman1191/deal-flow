"""
Tests for POST /leads/bulk-archive (issue #141, refined in the #141 follow-up):
multi-select archive that still fires the full Copper write-back (Unqualified
+ tags + a mandatory reason) per lead, exactly like the single archive-no-reply
path -- but WITHOUT that path's mandatory-rating gate, and with per-lead
isolation so one bad id / DB hiccup / Copper failure never aborts the rest of
the batch.

Unlike the original cut, the Unqualification Reasons (Copper custom field
244358) are no longer best-effort/AI-only: the caller must supply at least one
reason_option_id up front, applied to every lead in the batch, so every
bulk-archived lead is guaranteed a reason in Copper. The Unqualified Details
field (244359) prefers the caller's `note`; only when no note is given does it
fall back to a best-effort AI-generated detail.

Follows the TestClient + dependency-override pattern used throughout
test_copper_writebacks.py / test_archive_no_reply_gate.py.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import claude_agent, copper_writer
from app.services.auth import get_current_user

client = TestClient(app)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns queued results for each execute() call in order. Records
    commit/rollback counts so tests can assert the per-lead isolation path
    actually rolls back a poisoned transaction before moving to the next
    lead."""

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _obj):
        pass


def _fake_card(rated: bool, bucket: str = "REJECT"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        user_override=None,
        user_rating="up" if rated else None,
        confidence_score=70,
        summary="thin deep-tech signal",
        red_flags=[],
        scoring_breakdown={},
        research_data={},
    )


def _fake_lead(copper_id=None, copper_opportunity_id=None, status="assessed", lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=copper_id,
        copper_opportunity_id=copper_opportunity_id,
        company_name="Acme Deep Tech",
        raw_copper_data={"tags": ["existing-tag"]},
        pitch_deck_text=None,
        status=status,
    )


async def _fake_current_user():
    return SimpleNamespace(email="reviewer@raed.vc", is_active=True)


@pytest.fixture
def override_auth():
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _override_db(results):
    session = _FakeSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


def _no_ai_reason(monkeypatch):
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {"reason_option_ids": [367311], "detail_text": "Bulk cleared."},
    )


def test_bulk_archive_sets_all_leads_archived_and_enqueues_one_copper_writeback_each(
    override_auth, monkeypatch
):
    _no_ai_reason(monkeypatch)
    leads = [_fake_lead(copper_id=str(i)) for i in range(3)]
    cards = [_fake_card(rated=False) for _ in leads]  # unrated -- must not matter for bulk

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    results = []
    for lead, card in zip(leads, cards):
        results += [lead, card]
    session = _override_db(results)
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={
                "lead_ids": [str(lead.id) for lead in leads],
                "reason_option_ids": [367302],
            },
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body == {"archived": 3, "copper_enqueued": 3, "failed": []}
    assert all(lead.status == "archived" for lead in leads)
    assert len(calls) == 3
    assert {c[0] for c in calls} == {"0", "1", "2"}
    assert all(c[2] == {"reason_option_ids": [367302], "detail_text": "Bulk cleared."} for c in calls)


def test_bulk_archive_no_rating_required_unlike_single_archive(override_auth, monkeypatch):
    """The single-lead archive-no-reply path 428s an unrated card; bulk
    archive must bypass that gate entirely while still writing back to
    Copper."""
    _no_ai_reason(monkeypatch)
    lead = _fake_lead(copper_id="99")
    card = _fake_card(rated=False)

    calls = []
    monkeypatch.setattr(
        copper_writer, "archive_in_copper", lambda *a, **k: calls.append((a, k))
    )

    _override_db([lead, card])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 1, "failed": []}
    assert lead.status == "archived"
    assert len(calls) == 1


def test_bulk_archive_one_failing_lead_does_not_abort_the_batch(override_auth, monkeypatch):
    """Per-lead isolation: a DB blow-up while processing one lead must not
    prevent the others in the same batch from archiving + enqueueing."""
    _no_ai_reason(monkeypatch)
    good_lead_1 = _fake_lead(copper_id="1")
    good_card_1 = _fake_card(rated=True)
    bad_lead_id = uuid.uuid4()
    good_lead_2 = _fake_lead(copper_id="2")
    good_card_2 = _fake_card(rated=True)

    calls = []
    monkeypatch.setattr(
        copper_writer, "archive_in_copper", lambda *a, **k: calls.append(a)
    )

    class _RaisingLogEvent:
        """log_event raises only for the bad lead -- simulates a mid-batch
        DB error on that one lead's write while leaving the others intact."""

        async def __call__(self, db, lead_id, event_type, payload=None):
            if lead_id == bad_lead_id:
                raise RuntimeError("db exploded")

    monkeypatch.setattr("app.routers.leads.log_event", _RaisingLogEvent())

    # bad_lead_id has no matching Lead row queued as "found" with that id --
    # simulate the DB error happening on a lead that IS found, so log_event's
    # id check fires.
    bad_lead = _fake_lead(copper_id="3", lead_id=bad_lead_id)

    session = _override_db([good_lead_1, good_card_1, bad_lead, good_lead_2, good_card_2])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={
                "lead_ids": [str(good_lead_1.id), str(bad_lead_id), str(good_lead_2.id)],
                "reason_option_ids": [367302],
            },
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == 2
    assert body["copper_enqueued"] == 2
    assert len(body["failed"]) == 1
    assert body["failed"][0]["lead_id"] == str(bad_lead_id)
    assert "db exploded" in body["failed"][0]["error"]
    assert good_lead_1.status == "archived"
    assert good_lead_2.status == "archived"
    assert session.rollbacks == 1
    assert len(calls) == 2


def test_bulk_archive_not_found_lead_is_isolated_as_a_failure(override_auth):
    lead = _fake_lead(copper_id=None)
    session = _override_db([lead, None])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id), str(uuid.uuid4())], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["error"] == "not_found"


def test_bulk_archive_invalid_lead_id_is_isolated_and_never_hits_the_db(override_auth):
    lead = _fake_lead(copper_id=None)
    session = _override_db([lead])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": ["not-a-uuid", str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == 1
    assert body["failed"] == [{"lead_id": "not-a-uuid", "error": "invalid_lead_id"}]


def test_bulk_archive_already_archived_lead_is_idempotent_noop(override_auth):
    lead = _fake_lead(copper_id="42", status="archived")
    _override_db([lead])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 0, "failed": []}


def test_bulk_archive_no_copper_id_skips_write_but_still_archives(override_auth):
    lead = _fake_lead(copper_id=None)
    _override_db([lead])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 0, "failed": []}
    assert lead.status == "archived"


def test_bulk_archive_already_converted_lead_skips_copper_write(override_auth):
    lead = _fake_lead(copper_id="5", copper_opportunity_id="opp-1")
    _override_db([lead])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 0, "failed": []}


def test_bulk_archive_ai_reason_failure_is_best_effort_and_still_archives(
    override_auth, monkeypatch
):
    """The AI-generated detail note is still best-effort -- a failed AI call
    must never block the archive or Copper write. But (unlike before this
    refinement) the reason ids are NOT best-effort: they always come from the
    caller's request, so they still land in Copper even when the AI call
    fails."""
    def _boom(**kwargs):
        raise RuntimeError("deepseek down")

    monkeypatch.setattr(claude_agent, "generate_unqualification_reason", _boom)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    lead = _fake_lead(copper_id="7")
    card = _fake_card(rated=True)
    _override_db([lead, card])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json() == {"archived": 1, "copper_enqueued": 1, "failed": []}
    assert calls == [("7", ["existing-tag"], {"reason_option_ids": [367302], "detail_text": None})]


def test_bulk_archive_requires_at_least_one_reason(override_auth):
    """The Copper reason field must never be silently skipped for bulk
    archive -- the request is rejected before touching any lead if no reason
    option id is supplied."""
    response = client.post(
        "/api/v1/leads/bulk-archive",
        json={"lead_ids": [str(uuid.uuid4())], "reason_option_ids": []},
    )
    assert response.status_code == 422


def test_bulk_archive_rejects_unknown_reason_option_id(override_auth):
    response = client.post(
        "/api/v1/leads/bulk-archive",
        json={"lead_ids": [str(uuid.uuid4())], "reason_option_ids": [999999]},
    )
    assert response.status_code == 422


def test_bulk_archive_reason_option_ids_carried_on_every_copper_writeback(
    override_auth, monkeypatch
):
    """Explicit acceptance-criterion test: bulk-archiving N leads with a
    chosen reason enqueues N Copper write-backs, each carrying that reason in
    field 244358."""
    _no_ai_reason(monkeypatch)
    leads = [_fake_lead(copper_id=str(i)) for i in range(3)]
    cards = [_fake_card(rated=True) for _ in leads]

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    results = []
    for lead, card in zip(leads, cards):
        results += [lead, card]
    _override_db(results)
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={
                "lead_ids": [str(lead.id) for lead in leads],
                "reason_option_ids": [367301],
            },
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["copper_enqueued"] == 3
    assert len(calls) == 3
    assert all(kwargs["reason_option_ids"] == [367301] for _, _, kwargs in calls)


def test_bulk_archive_note_takes_precedence_over_ai_generated_detail(override_auth, monkeypatch):
    ai_calls = []

    def _tracked_ai(**kwargs):
        ai_calls.append(kwargs)
        return {"reason_option_ids": [367311], "detail_text": "AI-generated detail."}

    monkeypatch.setattr(claude_agent, "generate_unqualification_reason", _tracked_ai)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    lead = _fake_lead(copper_id="8")
    card = _fake_card(rated=True)
    _override_db([lead, card])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={
                "lead_ids": [str(lead.id)],
                "reason_option_ids": [367302],
                "note": "Manual note from the partner.",
            },
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert response.json()["copper_enqueued"] == 1
    assert ai_calls == []  # note provided -- AI detail generation is skipped entirely
    assert calls == [
        ("8", ["existing-tag"], {"reason_option_ids": [367302], "detail_text": "Manual note from the partner."})
    ]


def test_bulk_archive_falls_back_to_ai_detail_when_no_note_given(override_auth, monkeypatch):
    _no_ai_reason(monkeypatch)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    lead = _fake_lead(copper_id="9")
    card = _fake_card(rated=True)
    _override_db([lead, card])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive",
            json={"lead_ids": [str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code == 200
    assert calls == [("9", ["existing-tag"], {"reason_option_ids": [367302], "detail_text": "Bulk cleared."})]


def test_bulk_archive_non_admin_view_as_is_ignored_not_blocked(override_auth):
    """A non-admin's view_as is silently ignored (never honored), so it must
    NOT trigger the impersonation block -- only an honored view_as (admin)
    does that, exercised below."""
    lead = _fake_lead(copper_id=None)
    _override_db([lead])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive?view_as=someone-else@raed.vc",
            json={"lead_ids": [str(lead.id)], "reason_option_ids": [367302]},
        )
    finally:
        _clear_db_override()

    assert response.status_code != 403


def test_bulk_archive_blocked_while_impersonating_as_admin(monkeypatch):
    from app.config import settings

    async def _fake_admin_user():
        return SimpleNamespace(email=settings.owner_email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_admin_user
    _override_db([])
    try:
        response = client.post(
            "/api/v1/leads/bulk-archive?view_as=someone-else@raed.vc",
            json={"lead_ids": [str(uuid.uuid4())], "reason_option_ids": [367302]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        _clear_db_override()

    assert response.status_code == 403
    assert "Read-only while viewing another user's board" in response.json()["detail"]
