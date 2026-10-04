"""
Issue #177: surfacing a language mismatch (an English draft on an Arabic
lead, left over from before issue #168's detection fix) and a missing draft
(a YES/REJECT lead that somehow has no draft_body) on the assessment read
model, so a regression shows up as a field rather than a founder complaint.

Covers:
  1. `claude_agent.detect_draft_script` -- the pure deterministic script
     detector for an already-generated draft (mirrors detect_applicant_language).
  2. `language_audit.language_mismatch` / `language_audit.draft_missing` --
     the pure comparison helpers shared by the router and the backfill script.
     `draft_missing` must be False for MAYBE even with no draft_body -- a
     MAYBE legitimately has none (issue #177's critical guard).
  3. `GET /api/v1/assessments/{lead_id}` -- `language_mismatch` and
     `draft_missing` actually appear in the JSON payload with the right
     values, using the same fake-session TestClient pattern as
     test_stale_draft_guard.py.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import claude_agent, language_audit
from app.services.auth import get_current_user

client = TestClient(app)

# A company name with enough Arabic-script signal to clear the detector's
# minimum-signal floor on its own (mirrors test_draft_language.py's real
# issue-#168 shape).
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


# ---------- 1. detect_draft_script (pure, deterministic) ----------


def test_detects_arabic_draft():
    assert claude_agent.detect_draft_script(ARABIC_DRAFT_BODY) == "ar"


def test_detects_english_draft():
    assert claude_agent.detect_draft_script(ENGLISH_DRAFT_BODY) == "en"


def test_empty_draft_defaults_to_english():
    assert claude_agent.detect_draft_script("") == "en"
    assert claude_agent.detect_draft_script(None) == "en"


def test_stray_arabic_signature_in_english_draft_does_not_flip_result():
    # A handful of Arabic characters (well under the minimum signal floor)
    # inside an otherwise-English draft -- e.g. an Arabic contact name in the
    # greeting -- must not flip the whole draft to "ar".
    text = ENGLISH_DRAFT_BODY + " Regards, شركة"
    assert claude_agent.detect_draft_script(text) == "en"


# ---------- 2. language_audit.language_mismatch / draft_missing ----------


def _lead(company_name="Acme Deep Tech", description=ENGLISH_DESCRIPTION, pitch_deck_text=None):
    return SimpleNamespace(
        company_name=company_name,
        description=description,
        pitch_deck_text=pitch_deck_text,
        raw_copper_data=None,
    )


def test_language_mismatch_false_for_matching_english_draft():
    lead = _lead()
    assert language_audit.language_mismatch(lead, ENGLISH_DRAFT_BODY) is False


def test_language_mismatch_true_for_english_draft_on_arabic_lead():
    # Real pre-#168 shape: Arabic company name + English AI-written
    # enrichment in `description`, with an English draft left over from
    # before the detection fix.
    lead = _lead(company_name=ARABIC_COMPANY, description=ENGLISH_DESCRIPTION * 20)
    assert claude_agent.detect_applicant_language(language_audit.lead_language_fields(lead)) == "ar"
    assert language_audit.language_mismatch(lead, ENGLISH_DRAFT_BODY) is True


def test_language_mismatch_false_for_matching_arabic_draft():
    lead = _lead(company_name=ARABIC_COMPANY, description=ARABIC_DESCRIPTION)
    assert language_audit.language_mismatch(lead, ARABIC_DRAFT_BODY) is False


def test_language_mismatch_false_when_no_draft_at_all():
    lead = _lead(company_name=ARABIC_COMPANY, description=ARABIC_DESCRIPTION)
    assert language_audit.language_mismatch(lead, None) is False
    assert language_audit.language_mismatch(lead, "") is False


def test_draft_missing_true_for_reject_with_no_draft():
    assert language_audit.draft_missing("REJECT", None) is True
    assert language_audit.draft_missing("REJECT", "") is True


def test_draft_missing_true_for_yes_with_no_draft():
    assert language_audit.draft_missing("YES", None) is True


def test_draft_missing_false_for_maybe_with_no_draft():
    # Critical guard (issue #177): a MAYBE legitimately has no draft -- this
    # must NEVER be flagged, even though draft_body is empty.
    assert language_audit.draft_missing("MAYBE", None) is False
    assert language_audit.draft_missing("MAYBE", "") is False


def test_draft_missing_false_when_draft_present():
    assert language_audit.draft_missing("YES", "Hi there") is False
    assert language_audit.draft_missing("REJECT", "Hi there") is False


# ---------- 3. GET /assessments/{lead_id} -- field actually on the payload ----------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _FakeSession:
    def __init__(self, value):
        self._value = value

    async def execute(self, _query):
        return _FakeResult(self._value)

    def add(self, _obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _fake_card(bucket="YES", draft_type="meeting_request", draft_body="Hi there", user_override=None):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        confidence_score=80,
        summary="promising deep-tech team",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject="Subject" if draft_type else None,
        draft_body=draft_body,
        draft_type=draft_type,
        draft_bucket=bucket,
        research_sources=[],
        research_data={},
        assessed_without_deck=False,
        user_override=user_override,
        user_override_at=None,
        user_rating="up",
        user_rating_at=now,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )


def _fake_lead(lead_id, owner_email="owner@raed.vc", company_name="Acme Deep Tech", description=ENGLISH_DESCRIPTION):
    return SimpleNamespace(
        id=lead_id,
        owner_email=owner_email,
        copper_id=None,
        copper_opportunity_id=None,
        company_name=company_name,
        founder_names=["Founder One"],
        description=description,
        pitch_deck_text=None,
        raw_copper_data={"recipient_email": "founder@acme.test"},
        status="pending",
    )


def _auth(email="owner@raed.vc"):
    async def _fake_current_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_current_user


def _db(value):
    async def _fake_get_db():
        yield _FakeSession(value)

    app.dependency_overrides[get_db] = _fake_get_db


def _clear():
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_get_assessment_reports_language_mismatch_false_for_matching_draft():
    card = _fake_card(bucket="YES", draft_type="meeting_request", draft_body=ENGLISH_DRAFT_BODY)
    lead = _fake_lead(card.lead_id)
    _auth()
    _db((card, lead))
    try:
        response = client.get(f"/api/v1/assessments/{card.lead_id}")
    finally:
        _clear()

    assert response.status_code == 200
    body = response.json()
    assert body["language_mismatch"] is False
    assert body["draft_missing"] is False


def test_get_assessment_reports_draft_missing_true_for_reject_with_no_draft():
    card = _fake_card(bucket="REJECT", draft_type=None, draft_body=None)
    lead = _fake_lead(card.lead_id)
    _auth()
    _db((card, lead))
    try:
        response = client.get(f"/api/v1/assessments/{card.lead_id}")
    finally:
        _clear()

    assert response.status_code == 200
    body = response.json()
    assert body["draft_missing"] is True
    assert body["language_mismatch"] is False


def test_get_assessment_reports_draft_missing_false_for_maybe_with_no_draft():
    card = _fake_card(bucket="MAYBE", draft_type=None, draft_body=None)
    lead = _fake_lead(card.lead_id)
    _auth()
    _db((card, lead))
    try:
        response = client.get(f"/api/v1/assessments/{card.lead_id}")
    finally:
        _clear()

    assert response.status_code == 200
    body = response.json()
    assert body["draft_missing"] is False
