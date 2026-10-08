"""
Tests for issue #232: four prebuilt rejection templates (EN/AR), rendered
with zero LLM calls, replacing the AI-generated default for a REJECT draft.

Covers:
  1. `rejection_templates.select_template` -- reason -> template mapping,
     precedence, and the INTERNAL_ONLY_REASONS fallback to MANDATE.
  2. `rejection_templates.render_rejection_email` -- pure rendering: no
     network/LLM call, byte-identical copy outside the one reason
     sentence/clause, no re-engagement invitation, missing-first_name
     degradation, EN/AR placeholder substitution.
  3. POST /assessments/{lead_id}/rejection-template -- validation, zero-LLM
     guarantee (the `_chat_completion` choke point is never entered),
     persistence onto `card.rejection_reasons`/draft fields, language
     override vs. auto-detection.
  4. Bulk send reads whatever's on the card -- N leads rendered via the
     template endpoint and bulk-sent make zero LLM calls in total.
"""
from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import claude_agent, rejection_templates
from app.services.auth import get_current_user

client = TestClient(app)

REENGAGEMENT_PHRASES = [
    "if things change",
    "if things evolve",
    "reach back out",
    "reconnect",
    "keep us posted",
    "come back",
    "re-apply",
    "reapply",
]

# issue #239: an earlier build reintroduced a re-engagement invitation after
# it had been removed -- this is the regression test that stops a third
# round.
FORBIDDEN_PHRASES = [
    "تغيّرت",
    "مجدداً",
    "permanent no",
    "hear from you again",
]


# ---------------------------------------------------------------------------
# 1. select_template
# ---------------------------------------------------------------------------

def test_select_template_conflict_beats_market_size():
    assert rejection_templates.select_template(["Conflict of interest", "Market size"]) == "CONFLICT"


def test_select_template_market_size_beats_traction():
    assert rejection_templates.select_template(["Market size", "Lack of traction"]) == "MARKET_SIZE"


def test_select_template_traction_beats_mandate():
    assert rejection_templates.select_template(["Lack of traction", "Out of our stage"]) == "TRACTION"


def test_select_template_exit_potential_maps_to_market_size():
    assert rejection_templates.select_template(["Exit potential"]) == "MARKET_SIZE"


def test_select_template_business_model_maps_to_traction():
    assert rejection_templates.select_template(["Business Model"]) == "TRACTION"


def test_select_template_mandate_catchall_reasons():
    for reason in ("Out of our stage", "Out of our region", "Regulations and Legislation", "Technology and IP", "Other"):
        assert rejection_templates.select_template([reason]) == "MANDATE"


def test_select_template_internal_only_alone_falls_back_to_mandate():
    assert rejection_templates.select_template(["Founder(s)"]) == "MANDATE"
    assert rejection_templates.select_template(["Dedication and focus", "Ownership structure"]) == "MANDATE"


def test_select_template_no_reasons_falls_back_to_mandate():
    assert rejection_templates.select_template([]) == "MANDATE"
    assert rejection_templates.select_template(None) == "MANDATE"


def test_select_template_mixed_internal_and_shareable_ignores_internal():
    assert rejection_templates.select_template(["Founder(s)", "Conflict of interest"]) == "CONFLICT"


def test_every_unqual_reason_option_is_mapped_or_internal_only():
    covered = set(rejection_templates.REASON_TO_TEMPLATE) | set(claude_agent.INTERNAL_ONLY_REASONS)
    assert covered == set(claude_agent.UNQUAL_REASON_OPTIONS)


# ---------------------------------------------------------------------------
# 2. render_rejection_email
# ---------------------------------------------------------------------------

def _render(template_reason: str, language: str, first_name="Sara", partner_name="Reem"):
    return rejection_templates.render_rejection_email(
        first_name=first_name,
        company="Acme Deep Tech",
        partner_name=partner_name,
        reasons=[template_reason],
        language=language,
    )


def test_render_makes_no_llm_or_network_call(monkeypatch):
    def _fail_if_called(**_kwargs):
        raise AssertionError("_chat_completion must never be entered on the template render path")

    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_called)
    result = _render("Market size", "en")
    assert result["template"] == "MARKET_SIZE"


def test_all_four_english_bodies_identical_outside_reason_sentence():
    bodies = {
        key: rejection_templates.render_rejection_email(
            first_name="Sara", company="Acme", partner_name="Reem", reasons=[reason], language="en",
        )["body"]
        for key, reason in [
            ("CONFLICT", "Conflict of interest"),
            ("MARKET_SIZE", "Market size"),
            ("TRACTION", "Lack of traction"),
            ("MANDATE", "Out of our stage"),
        ]
    }
    stripped = {
        key: body.replace(rejection_templates._EN_REASONS[key], "<REASON>") for key, body in bodies.items()
    }
    values = list(stripped.values())
    assert all(v == values[0] for v in values), stripped


def test_all_four_arabic_bodies_identical_outside_reason_sentence():
    bodies = {
        key: rejection_templates.render_rejection_email(
            first_name="سارة", company="أكمي", partner_name="ريم", reasons=[reason], language="ar",
        )["body"]
        for key, reason in [
            ("CONFLICT", "Conflict of interest"),
            ("MARKET_SIZE", "Market size"),
            ("TRACTION", "Lack of traction"),
            ("MANDATE", "Out of our stage"),
        ]
    }
    stripped = {
        key: body.replace(rejection_templates._AR_REASONS[key], "<REASON>") for key, body in bodies.items()
    }
    values = list(stripped.values())
    assert all(v == values[0] for v in values), stripped


def test_conflict_and_market_size_together_renders_conflict_only_once():
    result = rejection_templates.render_rejection_email(
        first_name="Sara", company="Acme", partner_name="Reem",
        reasons=["Conflict of interest", "Market size"], language="en",
    )
    assert result["template"] == "CONFLICT"
    assert result["body"].count(rejection_templates._EN_REASONS["CONFLICT"]) == 1
    assert rejection_templates._EN_REASONS["MARKET_SIZE"] not in result["body"]


def test_founder_only_reason_renders_mandate_with_no_internal_wording():
    result = rejection_templates.render_rejection_email(
        first_name="Sara", company="Acme", partner_name="Reem", reasons=["Founder(s)"], language="en",
    )
    assert result["template"] == "MANDATE"
    assert "founder" not in result["body"].lower()


def test_no_rendered_body_invites_reapplication():
    for language in ("en", "ar"):
        for template_key in rejection_templates.TEMPLATE_KEYS:
            reasons_by_key = {v: k for k, v in rejection_templates.REASON_TO_TEMPLATE.items()}
            result = rejection_templates.render_rejection_email(
                first_name="Sara", company="Acme", partner_name="Reem",
                reasons=[reasons_by_key[template_key]], language=language,
            )
            lowered = result["body"].lower()
            for phrase in REENGAGEMENT_PHRASES:
                assert phrase not in lowered


def test_no_rendered_body_contains_forbidden_reengagement_wording():
    for language in ("en", "ar"):
        for template_key in rejection_templates.TEMPLATE_KEYS:
            reasons_by_key = {v: k for k, v in rejection_templates.REASON_TO_TEMPLATE.items()}
            result = rejection_templates.render_rejection_email(
                first_name="Sara", company="Acme", partner_name="Reem",
                reasons=[reasons_by_key[template_key]], language=language,
            )
            lowered = result["body"].lower()
            for phrase in FORBIDDEN_PHRASES:
                assert phrase not in lowered


def test_missing_first_name_degrades_to_usable_greeting_english():
    result = rejection_templates.render_rejection_email(
        first_name=None, company="Acme", partner_name="Reem", reasons=["Market size"], language="en",
    )
    assert "Hi ," not in result["body"]
    assert result["body"].startswith("Hi there,")


def test_blank_first_name_degrades_to_usable_greeting_arabic():
    result = rejection_templates.render_rejection_email(
        first_name="   ", company="Acme", partner_name="Reem", reasons=["Market size"], language="ar",
    )
    assert "مرحباً ،" not in result["body"]
    assert result["body"].startswith("مرحباً،")


def test_arabic_placeholders_substitute_correctly():
    result = rejection_templates.render_rejection_email(
        first_name="سارة", company="أكمي", partner_name="ريم", reasons=["Conflict of interest"], language="ar",
    )
    assert "سارة" in result["body"]
    assert "أكمي" in result["body"]
    assert "ريم" in result["body"]
    assert result["subject"] == "رائد فنتشرز — تحديث بخصوص طلبكم"
    assert "رائد فنتشرز" in result["body"]
    assert "رايد" not in result["body"]


def test_english_placeholders_substitute_correctly():
    result = _render("Market size", "en", first_name="Sara", partner_name="Reem")
    assert "Hi Sara," in result["body"]
    assert "Acme Deep Tech" in result["body"]
    assert "Reem" in result["body"]
    assert result["subject"] == "Raed Ventures — update on your application"


def test_invalid_language_raises():
    import pytest

    with pytest.raises(ValueError):
        rejection_templates.render_rejection_email(
            first_name="Sara", company="Acme", partner_name="Reem", reasons=[], language="fr",
        )


# ---------------------------------------------------------------------------
# 3. POST /assessments/{lead_id}/rejection-template
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
        return SimpleNamespace(email=email, is_active=True, full_name=None)

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
    base = dict(
        id=uuid.uuid4(),
        owner_email=None,
        company_name="Acme Deep Tech",
        founder_names=["Sara Founder"],
        description="An English-language description.",
        pitch_deck_text="deck text",
        raw_copper_data=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_card_row(bucket="REJECT", **overrides):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    base = dict(
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
        draft_bucket=None,
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
    base.update(overrides)
    return SimpleNamespace(**base)


def test_rejection_template_endpoint_rejects_non_reject_bucket():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="MAYBE")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{lead.id}/rejection-template")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None


def test_rejection_template_endpoint_too_many_reasons_is_400():
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


def test_rejection_template_endpoint_unknown_reason_is_400():
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


def test_rejection_template_endpoint_makes_no_llm_call(monkeypatch):
    def _fail_if_called(**_kwargs):
        raise AssertionError("_chat_completion must never be entered on the rejection-template send path")

    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_called)

    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"reasons": ["Market size"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["draft_type"] == "rejection"
    assert "market size" in body["draft_body"].lower() or "don't believe the market" in body["draft_body"].lower()


def test_rejection_template_endpoint_persists_reasons_and_draft_fields():
    lead = _fake_lead_row()
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
    body = response.json()
    assert card.rejection_reasons == ["Market size"]
    assert card.draft_type == "rejection"
    assert card.draft_bucket == "REJECT"
    assert body["rejection_reasons"] == ["Market size"]
    assert body["draft_language"] == "en"


def test_rejection_template_endpoint_founder_only_records_but_writes_mandate():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"reasons": ["Founder(s)"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert "founder" not in body["draft_body"].lower()
    assert body["rejection_reasons"] == ["Founder(s)"]
    assert card.rejection_reasons == ["Founder(s)"]


def test_rejection_template_endpoint_defaults_to_detected_arabic_language():
    lead = _fake_lead_row(
        description="شركة ناشئة تعمل في مجال التقنية في منطقة الشرق الأوسط وشمال أفريقيا",
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
    assert body["draft_language"] == "ar"
    assert bool(claude_agent._ARABIC_CHAR_RE.search(body["draft_body"]))


def test_rejection_template_endpoint_language_override_beats_detection():
    lead = _fake_lead_row(
        description="شركة ناشئة تعمل في مجال التقنية في منطقة الشرق الأوسط وشمال أفريقيا",
        pitch_deck_text="",
    )
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/rejection-template",
            json={"language": "en"},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert body["draft_language"] == "en"
    assert not claude_agent._ARABIC_CHAR_RE.search(body["draft_body"])


def test_rejection_template_endpoint_missing_founder_name_degrades_greeting():
    lead = _fake_lead_row(founder_names=[])
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
    assert "Hi ," not in body["draft_body"]


# ---------------------------------------------------------------------------
# 4. Bulk send reads whatever template-rendered content is on the card --
#    N leads rendered via the template endpoint, then bulk-sent, make zero
#    LLM calls in total (the endpoint above is already zero-LLM; bulk send
#    itself -- see bulk_rejection.py / test_send_bulk_rejection_task.py --
#    only ever reads card.draft_subject/draft_body, never calls the model).
# ---------------------------------------------------------------------------

def test_bulk_rejection_eligibility_accepts_a_template_rendered_draft():
    from app.services.bulk_rejection import eligibility_reason

    card = _fake_card_row(
        bucket="REJECT",
        draft_type="rejection",
        draft_body="Hi there,\n\n...",
        draft_bucket="REJECT",
    )
    lead = SimpleNamespace(
        raw_copper_data={"recipient_email": "founder@acme.test"},
        status="pending",
        copper_opportunity_id=None,
    )
    assert eligibility_reason(lead, card) is None
