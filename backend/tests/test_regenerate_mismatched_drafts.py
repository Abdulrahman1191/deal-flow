"""
Tests for scripts/regenerate_mismatched_drafts.py (issue #177).

plan_regeneration() is a pure function over already-fetched (card, lead)
rows (mirrors close_stale_copper.py's plan_cleanup() test pattern -- no live
DB, no LLM). apply_regeneration() is exercised with a fake regenerate_fn
(no LLM, no DB). main()/run() are exercised with a fake AsyncSessionLocal
(mirrors test_backfill_awaiting_deck.py), confirming dry-run makes no writes
and no regenerate_fn calls, and --commit regenerates only the mismatched
entries and leaves matching ones untouched.
"""
from __future__ import annotations
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import regenerate_mismatched_drafts as rmd  # noqa: E402

ARABIC_COMPANY = "كليفر ديزاين لخدمات مواقع"
ARABIC_DESCRIPTION = (
    "شركة ناشئة تعمل على تطوير تقنيات الذكاء الاصطناعي لتحليل البيانات الطبية "
    "في منطقة الشرق الأوسط وشمال أفريقيا"
)
ENGLISH_DESCRIPTION = "A deep-tech startup building AI-driven medical imaging tools for MENA hospitals."

ARABIC_DRAFT_BODY = (
    "مرحباً،\n\nشكراً لتقديم طلبكم إلى راعد فنتشرز. هل ترغبون في حجز مكالمة قصيرة؟ "
    "https://calendly.com/abdulrahman-raed/30min\n\nمع التحية،\nRaed Ventures"
)
ENGLISH_DRAFT_BODY = (
    "Hi there,\n\nThank you for applying to Raed Ventures. Would you like to book a "
    "short call? https://calendly.com/abdulrahman-raed/30min\n\nBest,\nRaed Ventures"
)


def _card(lead_id, bucket="YES", draft_body=ENGLISH_DRAFT_BODY, sent_at=None, approved_at=None,
          created_at=None, summary="promising team", user_override=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=lead_id,
        bucket=bucket,
        user_override=user_override,
        summary=summary,
        draft_type="meeting_request" if draft_body else None,
        draft_subject="Subject" if draft_body else None,
        draft_body=draft_body,
        draft_bucket=bucket,
        sent_at=sent_at,
        approved_at=approved_at,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _lead(company_name="Acme Deep Tech", description=ENGLISH_DESCRIPTION, status="pending", lead_id=None):
    return SimpleNamespace(
        id=lead_id or uuid.uuid4(),
        company_name=company_name,
        description=description,
        pitch_deck_text=None,
        raw_copper_data=None,
        status=status,
        owner_email="waleed@raed.vc",
    )


def _arabic_lead(**kwargs):
    kwargs.setdefault("company_name", ARABIC_COMPANY)
    kwargs.setdefault("description", ENGLISH_DESCRIPTION * 20)
    return _lead(**kwargs)


# --- plan_regeneration (pure) ------------------------------------------------


def test_plan_flags_english_draft_on_arabic_lead_as_mismatched():
    lead = _arabic_lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)

    plan = rmd.plan_regeneration([(card, lead)])

    assert len(plan) == 1
    assert plan[0]["mismatched"] is True
    assert plan[0]["lead_id"] == str(lead.id)


def test_plan_reports_matching_english_draft_as_no_change():
    lead = _lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)

    plan = rmd.plan_regeneration([(card, lead)])

    assert len(plan) == 1
    assert plan[0]["mismatched"] is False


def test_plan_skips_sent_card_even_when_mismatched():
    lead = _arabic_lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY, sent_at=datetime(2026, 6, 1, tzinfo=timezone.utc))

    assert rmd.plan_regeneration([(card, lead)]) == []


def test_plan_skips_approved_card_even_when_mismatched():
    lead = _arabic_lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY, approved_at=datetime(2026, 6, 1, tzinfo=timezone.utc))

    assert rmd.plan_regeneration([(card, lead)]) == []


def test_plan_skips_archived_leads():
    lead = _arabic_lead(status="archived")
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)

    assert rmd.plan_regeneration([(card, lead)]) == []


def test_plan_skips_cards_with_no_draft():
    lead = _lead()
    card = _card(lead.id, bucket="MAYBE", draft_body=None)

    assert rmd.plan_regeneration([(card, lead)]) == []


def test_plan_dedupes_to_latest_card_per_lead():
    lead = _lead(lead_id=uuid.uuid4())
    old_card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    new_card = _card(lead.id, draft_body=None, bucket="MAYBE", created_at=datetime(2026, 2, 1, tzinfo=timezone.utc))

    # Only the latest (MAYBE, no draft) should be considered -- and it's
    # skipped outright since there's nothing to regenerate.
    assert rmd.plan_regeneration([(old_card, lead), (new_card, lead)]) == []


# --- apply_regeneration (pure, fake regenerate_fn) ---------------------------


def test_apply_regenerates_only_mismatched_entries():
    arabic_lead = _arabic_lead()
    mismatched_card = _card(arabic_lead.id, draft_body=ENGLISH_DRAFT_BODY)
    english_lead = _lead()
    matching_card = _card(english_lead.id, draft_body=ENGLISH_DRAFT_BODY)

    plan = rmd.plan_regeneration([(mismatched_card, arabic_lead), (matching_card, english_lead)])

    calls = []

    def fake_regenerate(lead, bucket, summary):
        calls.append((lead.id, bucket))
        return {"draft_type": "meeting_request", "draft_subject": "موضوع", "draft_body": ARABIC_DRAFT_BODY}

    outcome = rmd.apply_regeneration(plan, fake_regenerate)

    assert calls == [(arabic_lead.id, "YES")]
    assert outcome["regenerated"] == [str(arabic_lead.id)]
    assert outcome["no_change"] == [str(english_lead.id)]
    assert outcome["failed"] == []
    assert mismatched_card.draft_body == ARABIC_DRAFT_BODY
    assert matching_card.draft_body == ENGLISH_DRAFT_BODY  # untouched


def test_apply_regeneration_is_idempotent_on_second_run():
    lead = _arabic_lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)
    plan = rmd.plan_regeneration([(card, lead)])

    rmd.apply_regeneration(
        plan,
        lambda lead, bucket, summary: {
            "draft_type": "meeting_request", "draft_subject": "موضوع", "draft_body": ARABIC_DRAFT_BODY,
        },
    )
    assert card.draft_body == ARABIC_DRAFT_BODY

    # Second run over the same (now-regenerated) card: no longer mismatched.
    second_plan = rmd.plan_regeneration([(card, lead)])
    assert second_plan[0]["mismatched"] is False

    def fail_if_called(*_a, **_k):
        raise AssertionError("must not regenerate an already-matching draft")

    outcome = rmd.apply_regeneration(second_plan, fail_if_called)
    assert outcome["regenerated"] == []
    assert outcome["no_change"] == [str(lead.id)]


def test_apply_one_failure_does_not_abort_the_rest():
    lead_a = _arabic_lead(lead_id=uuid.uuid4())
    card_a = _card(lead_a.id, draft_body=ENGLISH_DRAFT_BODY)
    lead_b = _arabic_lead(lead_id=uuid.uuid4())
    card_b = _card(lead_b.id, draft_body=ENGLISH_DRAFT_BODY)

    plan = rmd.plan_regeneration([(card_a, lead_a), (card_b, lead_b)])

    def fake_regenerate(lead, bucket, summary):
        if lead.id == lead_a.id:
            raise RuntimeError("LLM hiccup")
        return {"draft_type": "meeting_request", "draft_subject": "موضوع", "draft_body": ARABIC_DRAFT_BODY}

    outcome = rmd.apply_regeneration(plan, fake_regenerate)

    assert outcome["failed"] == [str(lead_a.id)]
    assert outcome["regenerated"] == [str(lead_b.id)]
    # The failed entry's draft is left exactly as it was -- next run retries it.
    assert card_a.draft_body == ENGLISH_DRAFT_BODY
    assert card_b.draft_body == ARABIC_DRAFT_BODY


# --- run()/main(): fake AsyncSessionLocal, dry-run vs --commit ---------------


class _FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeUsersResult:
    def __init__(self, users):
        self._users = users

    def scalars(self):
        return self._users


class _FakeSession:
    """`run()` issues at most two `execute()` calls in a fixed order: the
    (card, lead) rows query first, then (only when there's something
    mismatched to regenerate) the owner-fields-by-email query -- so the
    call count alone is enough to dispatch each fake result."""

    def __init__(self, rows, users=None):
        self._rows = rows
        self._users = users or []
        self.committed = 0
        self.execute_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _query):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _FakeRowsResult(self._rows)
        return _FakeUsersResult(self._users)

    async def commit(self):
        self.committed += 1


def _stub_session(monkeypatch, rows, users=None):
    session = _FakeSession(rows, users=users)
    monkeypatch.setattr(rmd, "AsyncSessionLocal", lambda: session)
    return session


class _FakeUserDb:
    """Minimal fake for _load_owner_fields_by_email's own unit tests --
    unlike _FakeSession, it's exercised directly (no `async with`) and only
    ever serves the one users query."""

    def __init__(self, users):
        self._users = users

    async def execute(self, _query):
        return _FakeUsersResult(self._users)


def test_main_dry_run_makes_no_writes_and_no_regenerate_calls(monkeypatch, capsys):
    lead = _arabic_lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)
    session = _stub_session(monkeypatch, [(card, lead)])

    def fail_if_called(*_a, **_k):
        raise AssertionError("dry run must not call the LLM-backed regenerate path")

    monkeypatch.setattr(rmd, "_regenerate_draft_for_bucket", fail_if_called)

    exit_code = rmd.main([])

    assert exit_code == 0
    assert session.committed == 0
    assert card.draft_body == ENGLISH_DRAFT_BODY  # untouched
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_main_commit_regenerates_mismatched_and_leaves_matching_alone(monkeypatch, capsys):
    arabic_lead = _arabic_lead(lead_id=uuid.uuid4())
    mismatched_card = _card(arabic_lead.id, draft_body=ENGLISH_DRAFT_BODY)
    english_lead = _lead(lead_id=uuid.uuid4())
    matching_card = _card(english_lead.id, draft_body=ENGLISH_DRAFT_BODY)

    session = _stub_session(monkeypatch, [(mismatched_card, arabic_lead), (matching_card, english_lead)])

    def fake_regenerate(lead, bucket, summary, owner_fields):
        return {"draft_type": "meeting_request", "draft_subject": "موضوع", "draft_body": ARABIC_DRAFT_BODY}

    monkeypatch.setattr(rmd, "_regenerate_draft_for_bucket", fake_regenerate)

    exit_code = rmd.main(["--commit"])

    assert exit_code == 0
    assert session.committed == 1
    assert mismatched_card.draft_body == ARABIC_DRAFT_BODY
    assert matching_card.draft_body == ENGLISH_DRAFT_BODY
    out = capsys.readouterr().out
    assert "Done. 1 regenerated, 1 unchanged, 0 failed." in out


def test_main_commit_with_nothing_mismatched_makes_no_writes(monkeypatch, capsys):
    lead = _lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)
    session = _stub_session(monkeypatch, [(card, lead)])

    def fail_if_called(*_a, **_k):
        raise AssertionError("nothing to regenerate -- must not call the LLM path")

    monkeypatch.setattr(rmd, "_regenerate_draft_for_bucket", fail_if_called)

    exit_code = rmd.main(["--commit"])

    assert exit_code == 0
    assert session.committed == 0
    assert "Nothing to regenerate." in capsys.readouterr().out


def test_main_commit_returns_nonzero_when_a_regeneration_fails(monkeypatch):
    lead = _arabic_lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)
    _stub_session(monkeypatch, [(card, lead)])

    def fail_regenerate(*_a, **_k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(rmd, "_regenerate_draft_for_bucket", fail_regenerate)

    exit_code = rmd.main(["--commit"])
    assert exit_code == 1


def test_fetch_rows_returns_whatever_the_query_yields():
    lead = _lead()
    card = _card(lead.id, draft_body=ENGLISH_DRAFT_BODY)
    session = _FakeSession([(card, lead)])

    rows = asyncio.run(rmd._fetch_rows(session, None))
    assert rows == [(card, lead)]


# --- _load_owner_fields_by_email / _make_regenerate_fn (issue #177 fix-round-1) --


def test_load_owner_fields_by_email_maps_known_owners_and_skips_unknown():
    waleed = SimpleNamespace(
        email="waleed@raed.vc", calendly_url="https://calendly.com/waleed-raed/30min", full_name="Waleed"
    )
    db = _FakeUserDb([waleed])

    fields = asyncio.run(rmd._load_owner_fields_by_email(db, {"waleed@raed.vc", "uday@raed.vc", None}))

    assert fields == {
        "waleed@raed.vc": {"owner_calendly": "https://calendly.com/waleed-raed/30min", "owner_name": "Waleed"},
    }


def test_load_owner_fields_by_email_skips_the_query_when_no_emails():
    db = _FakeUserDb([SimpleNamespace(email="waleed@raed.vc", calendly_url="x", full_name="Waleed")])

    fields = asyncio.run(rmd._load_owner_fields_by_email(db, set()))

    assert fields == {}


def test_make_regenerate_fn_passes_the_matching_owners_fields(monkeypatch):
    captured = {}

    def fake_regenerate_draft_for_bucket(lead, bucket, summary, owner_fields):
        captured["owner_fields"] = owner_fields
        return {"draft_type": "meeting_request", "draft_subject": "x", "draft_body": "y"}

    monkeypatch.setattr(rmd, "_regenerate_draft_for_bucket", fake_regenerate_draft_for_bucket)

    lead = _lead()  # owner_email="waleed@raed.vc"
    owner_fields_by_email = {
        "waleed@raed.vc": {"owner_calendly": "https://calendly.com/waleed-raed/30min", "owner_name": "Waleed"},
    }
    regenerate_fn = rmd._make_regenerate_fn(owner_fields_by_email)
    regenerate_fn(lead, "YES", "summary")

    assert captured["owner_fields"] == {
        "owner_calendly": "https://calendly.com/waleed-raed/30min", "owner_name": "Waleed",
    }


def test_make_regenerate_fn_falls_back_to_none_for_an_owner_with_no_user_row(monkeypatch):
    captured = {}

    def fake_regenerate_draft_for_bucket(lead, bucket, summary, owner_fields):
        captured["owner_fields"] = owner_fields
        return {"draft_type": "meeting_request", "draft_subject": "x", "draft_body": "y"}

    monkeypatch.setattr(rmd, "_regenerate_draft_for_bucket", fake_regenerate_draft_for_bucket)

    lead = _lead()  # owner_email="waleed@raed.vc", not present in the map below
    regenerate_fn = rmd._make_regenerate_fn({})
    regenerate_fn(lead, "YES", "summary")

    assert captured["owner_fields"] == {"owner_calendly": None, "owner_name": None}


def test_main_commit_regenerates_with_the_leads_owner_calendly_not_the_default(monkeypatch, capsys):
    """Regression for the fix-round-1 bug: _default_regenerate_fn used to
    call _regenerate_draft_for_bucket with owner_fields={}, which
    claude_agent.regenerate_draft maps to DEFAULT_CALENDLY_URL --
    abdulrahman's personal booking link -- even though every lead in this
    script's target population belongs to waleed/uday/yomna."""
    arabic_lead = _arabic_lead(lead_id=uuid.uuid4())  # owner_email="waleed@raed.vc"
    mismatched_card = _card(arabic_lead.id, draft_body=ENGLISH_DRAFT_BODY)
    waleed = SimpleNamespace(
        email="waleed@raed.vc", calendly_url="https://calendly.com/waleed-raed/30min", full_name="Waleed"
    )
    _stub_session(monkeypatch, [(mismatched_card, arabic_lead)], users=[waleed])

    captured = {}

    def fake_regenerate(lead, bucket, summary, owner_fields):
        captured["owner_fields"] = owner_fields
        return {"draft_type": "meeting_request", "draft_subject": "موضوع", "draft_body": ARABIC_DRAFT_BODY}

    monkeypatch.setattr(rmd, "_regenerate_draft_for_bucket", fake_regenerate)

    exit_code = rmd.main(["--commit"])

    assert exit_code == 0
    assert captured["owner_fields"] == {
        "owner_calendly": "https://calendly.com/waleed-raed/30min", "owner_name": "Waleed",
    }
    assert captured["owner_fields"]["owner_calendly"] != "https://calendly.com/abdulrahman-raed/30min"
