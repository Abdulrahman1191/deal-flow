"""
Tests for app.tasks.send_bulk_rejection (issue #205): the per-lead Celery
task dispatched by POST /leads/bulk-send-rejection.

Mirrors the _FakeTaskSession (entity-dispatched execute()) pattern from
test_bulk_archive_writeback_task.py / test_stale_draft_guard.py. `lead.
copper_id` is left unset throughout so these tests stay focused on the
send + finalize + idempotency + training-capture behavior this issue adds,
without re-covering the Copper write-back integration _finalize_sent already
has its own coverage for for elsewhere.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.assessment import AssessmentCard
from app.models.bulk_rejection import BulkRejectionBatchItem
from app.models.lead import Lead
from app.models.user import User
from app.services import email_sender
from app.tasks import send_bulk_rejection


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeTaskSession:
    def __init__(self, lead=None, card=None, owner=None, item=None):
        self.lead = lead
        self.card = card
        self.owner = owner
        self.item = item
        self.added: list = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is BulkRejectionBatchItem:
            return _FakeScalarResult(self.item)
        if entity is Lead:
            return _FakeScalarResult(self.lead)
        if entity is User:
            return _FakeScalarResult(self.owner)
        assert entity is AssessmentCard
        return _FakeScalarResult(self.card)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _fake_lead(*, lead_id=None, copper_id=None, owner_email=None, recipient="founder@acme.test", status="assessed"):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        copper_id=copper_id,
        copper_opportunity_id=None,
        owner_email=owner_email,
        company_name="Acme Deep Tech",
        raw_copper_data={"recipient_email": recipient} if recipient else {},
        status=status,
    )


def _fake_card(*, sent_at=None, draft_type="rejection", draft_body="Thanks for applying.",
               draft_bucket="REJECT", approved_at=None):
    return SimpleNamespace(
        bucket="REJECT",
        user_override=None,
        sent_at=sent_at,
        approved_at=approved_at,
        draft_type=draft_type,
        draft_subject="Re: your application",
        draft_body=draft_body,
        draft_bucket=draft_bucket,
        summary="Not a fit on stage.",
        red_flags=[],
    )


def _fake_item():
    return SimpleNamespace(status="queued", reason=None)


async def _noop_capture(db, **kwargs):
    pass


def _install_fake_session(monkeypatch, session):
    monkeypatch.setattr(send_bulk_rejection, "CelerySessionLocal", lambda: session)


# ---------------------------------------------------------------------------
# happy path: one email, finalize, training capture
# ---------------------------------------------------------------------------


def test_task_sends_email_finalizes_and_captures_bulk_send_rejection_trigger(monkeypatch):
    lead = _fake_lead()
    card = _fake_card()
    item = _fake_item()
    session = _FakeTaskSession(lead=lead, card=card, item=item)
    _install_fake_session(monkeypatch, session)

    sent_calls = []
    monkeypatch.setattr(
        email_sender, "send_email",
        lambda to, subject, body, **kw: sent_calls.append({"to": to, "subject": subject, "body": body, **kw}),
    )

    captured = []
    async def _fake_capture(db, *, lead, card, human_bucket, trigger, acted_by_email=None, **kw):
        captured.append((trigger, human_bucket, acted_by_email))
    monkeypatch.setattr(send_bulk_rejection, "capture_override", _fake_capture)

    batch_id = str(uuid.uuid4())
    result = asyncio.run(send_bulk_rejection._run(batch_id, str(lead.id), "reviewer@raed.vc"))

    assert result == {"batch_id": batch_id, "lead_id": str(lead.id), "status": "sent", "reason": None}
    assert len(sent_calls) == 1
    assert sent_calls[0]["to"] == "founder@acme.test"

    assert card.sent_at is not None
    assert lead.status == "archived"
    assert item.status == "sent"
    assert captured == [("bulk_send_rejection", "REJECT", "reviewer@raed.vc")]


# ---------------------------------------------------------------------------
# idempotency: already-sent lead is skipped, not resent
# ---------------------------------------------------------------------------


def test_task_skips_lead_already_sent_and_does_not_resend(monkeypatch):
    already_sent_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lead = _fake_lead()
    card = _fake_card(sent_at=already_sent_at)
    item = _fake_item()
    session = _FakeTaskSession(lead=lead, card=card, item=item)
    _install_fake_session(monkeypatch, session)

    sent_calls = []
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **kw: sent_calls.append((a, kw)))
    monkeypatch.setattr(send_bulk_rejection, "capture_override", _noop_capture)

    batch_id = str(uuid.uuid4())
    result = asyncio.run(send_bulk_rejection._run(batch_id, str(lead.id), "reviewer@raed.vc"))

    assert result == {"batch_id": batch_id, "lead_id": str(lead.id), "status": "skipped", "reason": "already_sent"}
    assert sent_calls == []
    assert card.sent_at == already_sent_at  # untouched
    assert item.status == "skipped"
    assert item.reason == "already_sent"


def test_task_commits_sent_at_before_finalize_so_a_redelivery_does_not_resend(monkeypatch):
    """Simulates the acks_late=True redelivery window this task is exposed
    to: a worker lost after send_email() succeeds but before _finalize_sent
    finishes its Copper/LLM work. The fix commits card.sent_at (and the batch
    item row) right after the send, before that slow work runs -- so by the
    time a redelivered task re-enters _run and re-checks eligibility, it
    sees sent_at already set and resolves as skipped/already_sent instead of
    calling send_email a second time."""
    from app.routers import assessments

    lead = _fake_lead()
    card = _fake_card()
    item = _fake_item()
    session = _FakeTaskSession(lead=lead, card=card, item=item)
    _install_fake_session(monkeypatch, session)

    sent_calls = []
    monkeypatch.setattr(
        email_sender, "send_email",
        lambda *a, **kw: sent_calls.append((a, kw)),
    )

    finalize_calls = []

    async def _fake_finalize_sent(db, card_arg, lead_arg, user_arg):
        # By the time the slow Copper-approve-write / unqualification-reason
        # LLM call (_finalize_sent's own work) would run, sent_at and the
        # batch item must already be committed -- that's what shrinks the
        # acks_late redelivery window to the SMTP call itself.
        assert card_arg.sent_at is not None
        assert item.status == "sent"
        finalize_calls.append(1)
        return {"status": "sent"}

    monkeypatch.setattr(assessments, "_finalize_sent", _fake_finalize_sent)
    monkeypatch.setattr(send_bulk_rejection, "capture_override", _noop_capture)

    batch_id = str(uuid.uuid4())
    result = asyncio.run(send_bulk_rejection._run(batch_id, str(lead.id), "reviewer@raed.vc"))

    assert finalize_calls == [1]
    assert result["status"] == "sent"
    assert len(sent_calls) == 1

    # Celery redelivers the same task -- e.g. the worker was lost right after
    # the SMTP call above, before this attempt's finalize work ran at all.
    redelivered_result = asyncio.run(send_bulk_rejection._run(batch_id, str(lead.id), "reviewer@raed.vc"))

    assert redelivered_result == {
        "batch_id": batch_id, "lead_id": str(lead.id), "status": "skipped", "reason": "already_sent",
    }
    assert len(sent_calls) == 1  # not resent


def test_task_re_checks_eligibility_stale_draft_guard(monkeypatch):
    """A lead whose draft_bucket disagrees with its current bucket must never
    be sent, even if it slipped through to a dispatched task (issue #150
    guard, exercised again at task time per the issue's defensive design)."""
    lead = _fake_lead()
    card = _fake_card(draft_bucket="YES")
    item = _fake_item()
    session = _FakeTaskSession(lead=lead, card=card, item=item)
    _install_fake_session(monkeypatch, session)

    sent_calls = []
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **kw: sent_calls.append((a, kw)))

    batch_id = str(uuid.uuid4())
    result = asyncio.run(send_bulk_rejection._run(batch_id, str(lead.id), "reviewer@raed.vc"))

    assert result["status"] == "skipped"
    assert result["reason"] == "stale_or_missing_draft"
    assert sent_calls == []
    assert card.sent_at is None


# ---------------------------------------------------------------------------
# SMTP failure on one lead is isolated and reported
# ---------------------------------------------------------------------------


def test_task_smtp_failure_is_reported_and_does_not_finalize(monkeypatch):
    lead = _fake_lead()
    card = _fake_card()
    item = _fake_item()
    session = _FakeTaskSession(lead=lead, card=card, item=item)
    _install_fake_session(monkeypatch, session)

    def _boom(*a, **kw):
        raise RuntimeError("SMTP connection refused")

    monkeypatch.setattr(email_sender, "send_email", _boom)
    captured = []
    monkeypatch.setattr(send_bulk_rejection, "capture_override", lambda *a, **kw: captured.append(1))

    batch_id = str(uuid.uuid4())
    result = asyncio.run(send_bulk_rejection._run(batch_id, str(lead.id), "reviewer@raed.vc"))

    assert result["status"] == "failed"
    assert "send_failed" in result["reason"]
    assert "SMTP connection refused" in result["reason"]
    assert card.sent_at is None
    assert lead.status == "assessed"  # never finalized/archived
    assert item.status == "failed"
    assert captured == []


def test_task_wrapper_catches_exceptions_and_returns_failed(monkeypatch):
    def _boom(_batch_id, _lead_id, _actor_email):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(send_bulk_rejection, "_run", _boom)

    result = send_bulk_rejection.send_bulk_rejection_task("batch-1", "lead-1", "reviewer@raed.vc")

    assert result["batch_id"] == "batch-1"
    assert result["lead_id"] == "lead-1"
    assert result["status"] == "failed"
    assert "db exploded" in result["error"]


# ---------------------------------------------------------------------------
# one email per lead -- no multi-recipient / cross-lead leakage
# ---------------------------------------------------------------------------


def test_task_sends_exactly_one_recipient_and_never_leaks_across_leads(monkeypatch):
    lead_a = _fake_lead(recipient="founder-a@acme.test")
    card_a = _fake_card()
    lead_b = _fake_lead(recipient="founder-b@beta.test")
    card_b = _fake_card()

    sent_calls = []
    monkeypatch.setattr(
        email_sender, "send_email",
        lambda to, subject, body, **kw: sent_calls.append({"to": to, "bcc": kw.get("bcc")}),
    )
    monkeypatch.setattr(send_bulk_rejection, "capture_override", _noop_capture)

    for lead, card in [(lead_a, card_a), (lead_b, card_b)]:
        session = _FakeTaskSession(lead=lead, card=card, item=_fake_item())
        _install_fake_session(monkeypatch, session)
        asyncio.run(send_bulk_rejection._run(str(uuid.uuid4()), str(lead.id), "reviewer@raed.vc"))

    assert len(sent_calls) == 2
    assert sent_calls[0]["to"] == "founder-a@acme.test"
    assert sent_calls[1]["to"] == "founder-b@beta.test"
    # Single string, never a comma-joined multi-recipient header.
    assert "," not in sent_calls[0]["to"]
    assert "," not in sent_calls[1]["to"]
    # No founder ever appears as a Bcc/Cc on the other founder's message.
    assert sent_calls[0]["bcc"] != "founder-b@beta.test"
    assert sent_calls[1]["bcc"] != "founder-a@acme.test"


# ---------------------------------------------------------------------------
# missing lead/assessment is reported, not silently dropped
# ---------------------------------------------------------------------------


def test_task_missing_lead_is_reported_as_failed(monkeypatch):
    item = _fake_item()
    session = _FakeTaskSession(lead=None, card=None, item=item)
    _install_fake_session(monkeypatch, session)

    batch_id = str(uuid.uuid4())
    lead_id = str(uuid.uuid4())
    result = asyncio.run(send_bulk_rejection._run(batch_id, lead_id, "reviewer@raed.vc"))

    assert result["status"] == "failed"
    assert result["reason"] == "lead_or_assessment_missing"
    assert item.status == "failed"


# ---------------------------------------------------------------------------
# issue #232: bulk send of N leads makes zero LLM calls -- the task only
# ever ships whatever rejection_templates (or a prior template/AI
# regeneration) already wrote onto card.draft_body; it never calls the
# model itself.
# ---------------------------------------------------------------------------


def test_bulk_send_of_n_leads_produces_n_emails_and_zero_llm_calls(monkeypatch):
    from app.services import claude_agent

    def _fail_if_called(**_kwargs):
        raise AssertionError("_chat_completion must never be entered on the bulk send path")

    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_called)
    monkeypatch.setattr(send_bulk_rejection, "capture_override", _noop_capture)

    sent_calls = []
    monkeypatch.setattr(
        email_sender, "send_email",
        lambda to, subject, body, **kw: sent_calls.append({"to": to, "subject": subject, "body": body}),
    )

    leads_and_cards = [
        (_fake_lead(recipient=f"founder-{i}@acme.test"), _fake_card(draft_body=f"Templated rejection #{i}."))
        for i in range(5)
    ]

    for lead, card in leads_and_cards:
        session = _FakeTaskSession(lead=lead, card=card, item=_fake_item())
        _install_fake_session(monkeypatch, session)
        result = asyncio.run(send_bulk_rejection._run(str(uuid.uuid4()), str(lead.id), "reviewer@raed.vc"))
        assert result["status"] == "sent"

    assert len(sent_calls) == 5
    assert {c["to"] for c in sent_calls} == {f"founder-{i}@acme.test" for i in range(5)}
    # Each email is the distinct templated body that was on its own card --
    # one separate email per lead, never a shared/merged draft.
    assert {c["body"] for c in sent_calls} == {f"Templated rejection #{i}." for i in range(5)}
