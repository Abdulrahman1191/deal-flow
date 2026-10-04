"""
Router-level tests for issue #205: POST /leads/bulk-send-rejection/preview,
POST /leads/bulk-send-rejection, and GET /leads/bulk-send-rejection/{batch_id}.

Follows the TestClient + dependency-override + queued-fake-session pattern
used throughout test_bulk_archive.py / test_bulk_reassign.py -- no live
Postgres needed. The actual send + finalize happen in
send_bulk_rejection_task (see test_send_bulk_rejection_task.py); these tests
only cover the HTTP layer: eligibility reporting, confirm_count, throttled
dispatch, and the admin/view_as gates.
"""
from __future__ import annotations
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.bulk_rejection import BulkRejectionBatchItem
from app.services.auth import get_current_user
from app.tasks.send_bulk_rejection import send_bulk_rejection_task

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "waleed@raed.vc"

PREVIEW_URL = "/api/v1/leads/bulk-send-rejection/preview"
SEND_URL = "/api/v1/leads/bulk-send-rejection"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else []


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.added: list = []
        self.commits = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _fake_lead(*, lead_id=None, status="assessed", copper_opportunity_id=None, recipient="founder@acme.test"):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        owner_email=OWNER_EMAIL,
        company_name="Acme Deep Tech",
        status=status,
        copper_opportunity_id=copper_opportunity_id,
        raw_copper_data={"recipient_email": recipient} if recipient else {},
    )


def _fake_card(*, bucket="REJECT", sent_at=None, draft_type="rejection", draft_body="Thanks for applying.",
               draft_bucket="REJECT", draft_subject="Re: your application"):
    return SimpleNamespace(
        bucket=bucket,
        user_override=None,
        sent_at=sent_at,
        draft_type=draft_type,
        draft_body=draft_body,
        draft_bucket=draft_bucket,
        draft_subject=draft_subject,
    )


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(results):
    session = _FakeSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    _clear_auth()
    _clear_db()


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_returns_eligibility_and_excerpt_and_sends_nothing():
    eligible_lead = _fake_lead()
    eligible_card = _fake_card()
    stale_lead = _fake_lead()
    stale_card = _fake_card(draft_bucket="YES")  # issue #150 guard: bucket drifted

    _auth_as(OWNER_EMAIL)
    _use_db([eligible_lead, eligible_card, stale_lead, stale_card])

    response = client.post(PREVIEW_URL, json={"lead_ids": [str(eligible_lead.id), str(stale_lead.id)]})

    assert response.status_code == 200
    body = response.json()
    assert body["eligible_count"] == 1
    items = {item["lead_id"]: item for item in body["items"]}

    good = items[str(eligible_lead.id)]
    assert good["eligible"] is True
    assert good["reason"] is None
    assert good["company_name"] == "Acme Deep Tech"
    assert good["recipient_email"] == "founder@acme.test"
    assert good["draft_excerpt"] == "Thanks for applying."

    bad = items[str(stale_lead.id)]
    assert bad["eligible"] is False
    assert bad["reason"] == "stale_or_missing_draft"


def test_preview_reports_not_found_lead():
    _auth_as(OWNER_EMAIL)
    missing_id = uuid.uuid4()
    _use_db([None])

    response = client.post(PREVIEW_URL, json={"lead_ids": [str(missing_id)]})

    assert response.status_code == 200
    body = response.json()
    assert body["eligible_count"] == 0
    assert body["items"] == [{
        "lead_id": str(missing_id), "company_name": None, "recipient_email": None,
        "draft_subject": None, "draft_excerpt": None, "eligible": False, "reason": "not_found",
    }]


def test_preview_reports_invalid_lead_id_without_touching_db():
    _auth_as(OWNER_EMAIL)
    _use_db([])  # no queries should be issued for a malformed id

    response = client.post(PREVIEW_URL, json={"lead_ids": ["not-a-uuid"]})

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["reason"] == "invalid_lead_id"


def test_preview_forbidden_for_non_owner():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    response = client.post(PREVIEW_URL, json={"lead_ids": []})
    assert response.status_code == 403


def test_preview_blocked_while_impersonating_as_admin():
    _auth_as(OWNER_EMAIL)
    _use_db([])
    response = client.post(f"{PREVIEW_URL}?view_as=someone-else@raed.vc", json={"lead_ids": []})
    assert response.status_code == 403
    assert "Read-only while viewing another user's board" in response.json()["detail"]


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


def _record_apply_async(monkeypatch):
    calls = []
    monkeypatch.setattr(
        send_bulk_rejection_task, "apply_async",
        lambda args, countdown: calls.append({"args": args, "countdown": countdown}),
    )
    return calls


def test_send_dispatches_one_throttled_task_per_eligible_lead(monkeypatch):
    calls = _record_apply_async(monkeypatch)
    lead1, card1 = _fake_lead(), _fake_card()
    lead2, card2 = _fake_lead(), _fake_card()

    _auth_as(OWNER_EMAIL)
    session = _use_db([lead1, card1, lead2, card2])

    response = client.post(SEND_URL, json={
        "lead_ids": [str(lead1.id), str(lead2.id)], "confirm_count": 2,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 2
    assert body["skipped"] == []

    assert [c["countdown"] for c in calls] == [0, settings.bulk_rejection_send_interval_seconds]
    assert {c["args"][1] for c in calls} == {str(lead1.id), str(lead2.id)}
    assert all(c["args"][2] == OWNER_EMAIL for c in calls)
    batch_id = body["batch_id"]
    assert all(c["args"][0] == batch_id for c in calls)

    queued_items = [o for o in session.added if isinstance(o, BulkRejectionBatchItem)]
    assert len(queued_items) == 2
    assert all(item.status == "queued" for item in queued_items)
    assert all(item.owner_email == OWNER_EMAIL for item in queued_items)
    assert session.commits == 1


def test_send_confirm_count_mismatch_sends_nothing(monkeypatch):
    calls = _record_apply_async(monkeypatch)
    lead, card = _fake_lead(), _fake_card()

    _auth_as(OWNER_EMAIL)
    session = _use_db([lead, card])

    response = client.post(SEND_URL, json={"lead_ids": [str(lead.id)], "confirm_count": 2})

    assert response.status_code == 409
    assert calls == []
    assert session.added == []
    assert session.commits == 0


def test_send_records_skipped_ineligible_leads(monkeypatch):
    calls = _record_apply_async(monkeypatch)
    ok_lead, ok_card = _fake_lead(), _fake_card()
    import datetime
    sent_lead, sent_card = _fake_lead(), _fake_card(sent_at=datetime.datetime(2026, 1, 1))

    _auth_as(OWNER_EMAIL)
    session = _use_db([ok_lead, ok_card, sent_lead, sent_card])

    response = client.post(SEND_URL, json={
        "lead_ids": [str(ok_lead.id), str(sent_lead.id)], "confirm_count": 1,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 1
    assert body["skipped"] == [{"lead_id": str(sent_lead.id), "reason": "already_sent"}]
    assert len(calls) == 1

    statuses = {o.lead_id: o.status for o in session.added if isinstance(o, BulkRejectionBatchItem)}
    assert statuses[ok_lead.id] == "queued"
    assert statuses[sent_lead.id] == "skipped"


def test_send_forbidden_for_non_owner(monkeypatch):
    calls = _record_apply_async(monkeypatch)
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    response = client.post(SEND_URL, json={"lead_ids": [], "confirm_count": 0})
    assert response.status_code == 403
    assert calls == []


def test_send_blocked_while_impersonating_as_admin(monkeypatch):
    calls = _record_apply_async(monkeypatch)
    _auth_as(OWNER_EMAIL)
    _use_db([])
    response = client.post(
        f"{SEND_URL}?view_as=someone-else@raed.vc", json={"lead_ids": [], "confirm_count": 0},
    )
    assert response.status_code == 403
    assert calls == []


# ---------------------------------------------------------------------------
# batch status
# ---------------------------------------------------------------------------


def _fake_batch_row(batch_id, owner_email=OWNER_EMAIL, status="sent", reason=None):
    return SimpleNamespace(
        batch_id=batch_id, lead_id=uuid.uuid4(), owner_email=owner_email,
        company_name="Acme Deep Tech", status=status, reason=reason,
    )


def test_batch_status_returns_counts_and_items():
    batch_id = uuid.uuid4()
    rows = [
        _fake_batch_row(batch_id, status="sent"),
        _fake_batch_row(batch_id, status="failed", reason="send_failed: boom"),
        _fake_batch_row(batch_id, status="skipped", reason="already_sent"),
    ]
    _auth_as(OWNER_EMAIL)
    _use_db([rows])

    response = client.get(f"/api/v1/leads/bulk-send-rejection/{batch_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] == 1
    assert body["failed"] == 1
    assert body["skipped"] == 1
    assert body["queued"] == 0
    assert len(body["items"]) == 3


def test_batch_status_404_for_unknown_or_other_owners_batch():
    _auth_as(OWNER_EMAIL)
    _use_db([[]])
    response = client.get(f"/api/v1/leads/bulk-send-rejection/{uuid.uuid4()}")
    assert response.status_code == 404


def test_batch_status_forbidden_for_non_owner():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db([])
    response = client.get(f"/api/v1/leads/bulk-send-rejection/{uuid.uuid4()}")
    assert response.status_code == 403
