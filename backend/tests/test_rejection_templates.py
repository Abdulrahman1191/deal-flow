"""
Tests for issue #232: prebuilt rejection email templates (EN/AR), no LLM call
on send.

Covers:
  1. rejection_templates.render_rejection_email -- a pure function, no
     network/LLM call (asserts claude_agent._chat_completion is never
     entered).
  2. All four English bodies (and all four Arabic bodies) are byte-identical
     outside the one reason clause/sentence.
  3. Reason -> template precedence (CONFLICT > MARKET_SIZE > TRACTION >
     MANDATE), including a mix that should render exactly one reason.
  4. INTERNAL_ONLY_REASONS never select a template; falls back to MANDATE
     when they're the only reasons picked, and never appear in the body.
  5. Language selection (detect_applicant_language default + explicit
     override) and placeholder substitution in both scripts.
  6. A missing first_name degrades to a usable greeting.
  7. Neither language invites the founder back.
  8. POST /assessments/{lead_id}/rejection-template -- validation, persists
     rejection_reasons, wires into the same card fields bulk rejection reads,
     and never touches the LLM.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import claude_agent, rejection_templates
from app.services.auth import get_current_user

client = TestClient(app)

ALL_TEMPLATE_KEYS = ("CONFLICT", "MARKET_SIZE", "TRACTION", "MANDATE")

REENGAGEMENT_PHRASES = [
    "if things change",
    "if things evolve",
    "keep us posted",
    "reach back out",
    "reconnect",
    "hear from you again",
    "feel free to",
]


def _fail_if_llm_called(**_kwargs):
    raise AssertionError("_chat_completion must never be entered on the template render path")


# ---------------------------------------------------------------------------
# 1. Zero-LLM guarantee
# ---------------------------------------------------------------------------

def test_render_makes_no_llm_call(monkeypatch):
    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_llm_called)
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="en", first_name="Jane", company="Acme",
    )
    assert result["template_key"] == "MARKET_SIZE"


# ---------------------------------------------------------------------------
# 2. All four bodies identical outside the one reason clause/sentence
# ---------------------------------------------------------------------------

def _render_all(language: str) -> dict[str, str]:
    return {
        key: rejection_templates.render_rejection_email(
            reasons=[r for r, k in rejection_templates.REASON_TEMPLATE_MAP.items() if k == key][:1],
            language=language,
            first_name="Jane",
            company="Acme",
            partner_name="Sam Partner",
        )["body"]
        for key in ALL_TEMPLATE_KEYS
    }


def test_english_bodies_identical_outside_reason_clause():
    bodies = _render_all("en")
    stripped = {
        key: body.replace(rejection_templates.EN_REASON_CLAUSES[key], "{REASON}")
        for key, body in bodies.items()
    }
    assert len(set(stripped.values())) == 1


def test_arabic_bodies_identical_outside_reason_sentence():
    bodies = _render_all("ar")
    stripped = {
        key: body.replace(rejection_templates.AR_REASON_SENTENCES[key], "{REASON}")
        for key, body in bodies.items()
    }
    assert len(set(stripped.values())) == 1


# ---------------------------------------------------------------------------
# 3. Precedence
# ---------------------------------------------------------------------------

def test_conflict_and_market_size_together_renders_conflict_only_once():
    result = rejection_templates.render_rejection_email(
        reasons=["Conflict of interest", "Market size"],
        language="en",
        first_name="Jane",
        company="Acme",
    )
    assert result["template_key"] == "CONFLICT"
    body = result["body"]
    assert body.count(rejection_templates.EN_REASON_CLAUSES["CONFLICT"]) == 1
    assert rejection_templates.EN_REASON_CLAUSES["MARKET_SIZE"] not in body


def test_select_template_key_precedence_order():
    assert rejection_templates.select_template_key(["Market size", "Lack of traction"]) == "MARKET_SIZE"
    assert rejection_templates.select_template_key(["Lack of traction", "Other"]) == "TRACTION"
    assert rejection_templates.select_template_key(["Out of our region"]) == "MANDATE"


# ---------------------------------------------------------------------------
# 4. Internal-only reasons
# ---------------------------------------------------------------------------

def test_internal_only_reason_alone_falls_back_to_mandate():
    assert rejection_templates.select_template_key(["Founder(s)"]) == "MANDATE"
    assert rejection_templates.select_template_key([]) == "MANDATE"
    assert rejection_templates.select_template_key(None) == "MANDATE"


def test_no_internal_only_wording_in_any_rendered_body():
    internal_phrases = ["founder", "dedication", "focus", "ownership"]
    for key in ALL_TEMPLATE_KEYS:
        for lang in ("en", "ar"):
            body = rejection_templates.render_rejection_email(
                reasons=["Founder(s)"], language=lang, first_name="Jane", company="Acme",
            )["body"]
            lowered = body.lower()
            for phrase in internal_phrases:
                assert phrase not in lowered


# ---------------------------------------------------------------------------
# 5. Language selection + placeholder substitution
# ---------------------------------------------------------------------------

def test_placeholders_substitute_in_english():
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="en", first_name="Jane", company="Acme Deep Tech",
        partner_name="Sam Partner",
    )
    assert "Hi Jane," in result["body"]
    assert "Acme Deep Tech" in result["body"]
    assert "Sam Partner" in result["body"]
    assert result["subject"] == "Raed Ventures — update on your application"


def test_placeholders_substitute_in_arabic():
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="ar", first_name="جين", company="شركة أكمي",
        partner_name="سام",
    )
    assert "مرحباً جين،" in result["body"]
    assert "شركة أكمي" in result["body"]
    assert "سام" in result["body"]
    assert result["subject"] == "رائد فنتشرز — تحديث بخصوص طلبكم"


def test_arabic_firm_name_has_hamza_everywhere():
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="ar", first_name="جين", company="شركة أكمي",
    )
    assert "رائد" in result["subject"]
    assert "رايد" not in result["subject"]
    assert "رائد" in result["body"]
    assert "رايد" not in result["body"]


def test_arabic_pronoun_is_feminine():
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="ar", first_name="جين", company="شركة أكمي",
    )
    assert "اطّلعنا عليها" in result["body"]
    assert "اطّلعنا عليه " not in result["body"]


def test_language_defaults_to_en_for_unknown_value():
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="something-else", first_name="Jane", company="Acme",
    )
    assert result["language"] == "en"


# ---------------------------------------------------------------------------
# 6. Missing first_name degrades gracefully
# ---------------------------------------------------------------------------

def test_missing_first_name_degrades_to_usable_greeting_english():
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="en", first_name=None, company="Acme",
    )
    assert "Hi ," not in result["body"]
    assert "Hi there," in result["body"]


def test_missing_first_name_degrades_to_usable_greeting_arabic():
    result = rejection_templates.render_rejection_email(
        reasons=["Market size"], language="ar", first_name=None, company="شركة أكمي",
    )
    assert "مرحباً ،" not in result["body"]
    assert "مرحباً،" in result["body"]


def test_first_name_from_founders_extracts_first_token():
    assert rejection_templates.first_name_from_founders(["Jane Doe"]) == "Jane"
    assert rejection_templates.first_name_from_founders(["  "]) is None
    assert rejection_templates.first_name_from_founders(None) is None
    assert rejection_templates.first_name_from_founders([]) is None


# ---------------------------------------------------------------------------
# 7. No invitation to re-apply, in either language, across all four templates
# ---------------------------------------------------------------------------

def test_no_rendered_body_invites_re_application():
    for key_reason in rejection_templates.REASON_TEMPLATE_MAP:
        for lang in ("en", "ar"):
            body = rejection_templates.render_rejection_email(
                reasons=[key_reason], language=lang, first_name="Jane", company="Acme",
            )["body"]
            lowered = body.lower()
            for phrase in REENGAGEMENT_PHRASES:
                assert phrase not in lowered, f"found {phrase!r} in {lang} body for reason {key_reason!r}"


# ---------------------------------------------------------------------------
# 8. POST /assessments/{lead_id}/rejection-template
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(results):
    session = _RecordingSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def _fake_lead_row(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        owner_email=None,
        company_name="Acme Deep Tech",
        founder_names=["Jane Founder"],
        description="An English-language description.",
        pitch_deck_text="deck text",
        raw_copper_data=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_card_row(bucket="REJECT", **overrides):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        confidence_score=60,
        summary="not a fit on stage",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject=None,
        draft_body=None,
        draft_type=None,
        research_sources=[],
        research_data={},
        assessed_without_deck=False,
        user_override=None,
        user_override_at=None,
        rejection_reasons=None,
        user_rating="up",
        user_rating_at=now,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_rejection_template_endpoint_renders_and_persists(monkeypatch):
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_llm_called)

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"reasons": ["Market size"], "language": "en"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["draft_type"] == "rejection"
    assert body["rejection_reasons"] == ["Market size"]
    assert body["rejection_template_key"] == "MARKET_SIZE"
    assert body["rejection_template_language"] == "en"
    assert "Jane," in body["draft_body"]
    assert card.draft_bucket == "REJECT"
    assert card.draft_type == "rejection"
    assert card.rejection_reasons == ["Market size"]


def test_rejection_template_endpoint_wrong_bucket_is_400():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="YES")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{lead.id}/rejection-template")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_rejection_template_endpoint_more_than_three_reasons_is_400_and_writes_nothing():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"reasons": ["Market size", "Business Model", "Exit potential", "Technology and IP"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_rejection_template_endpoint_unknown_reason_is_400_and_writes_nothing():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"reasons": ["Not a real reason"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_rejection_template_endpoint_invalid_language_is_400():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"language": "fr"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_rejection_template_endpoint_defaults_to_detected_arabic():
    lead = _fake_lead_row(
        company_name="شركة التقنية",
        description="شركة ناشئة تعمل في مجال التقنية في منطقة الشرق الأوسط وشمال أفريقيا بشكل كامل وواضح",
        pitch_deck_text="",
    )
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{lead.id}/rejection-template")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["rejection_template_language"] == "ar"
    assert "رائد فنتشرز" in body["draft_body"]


def test_rejection_template_endpoint_internal_only_reason_falls_back_to_mandate_and_is_recorded():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"reasons": ["Founder(s)"], "language": "en"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["rejection_template_key"] == "MANDATE"
    assert body["rejection_reasons"] == ["Founder(s)"]
    assert "founder" not in body["draft_body"].lower()


def test_rejection_template_endpoint_sendable_by_bulk_rejection_afterward():
    """The card fields this endpoint writes are exactly what
    bulk_rejection.eligibility_reason checks -- draft_body, draft_type ==
    "rejection", draft_bucket == effective bucket -- so wiring templates in
    here makes bulk send pick them up with no changes of its own (issue
    #232's "wire it into both send paths")."""
    from app.services import bulk_rejection

    lead = _fake_lead_row(
        raw_copper_data={"recipient_email": "founder@acme.test"},
        status="pending",
        copper_opportunity_id=None,
    )
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"reasons": ["Market size"], "language": "en"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert bulk_rejection.eligibility_reason(lead, card) is None
