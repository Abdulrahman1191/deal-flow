"""
Tests for issue #232: four prebuilt rejection templates (EN/AR), selected by
the partner's canonical UNQUAL_REASON_OPTIONS pick, rendered with zero LLM
calls -- app.services.rejection_templates.

Covers every acceptance criterion listed in the issue:
  1. Rendering a template makes no LLM call.
  2. All four English bodies (and all four Arabic bodies) are identical
     outside the one reason sentence.
  3. "Conflict of interest" + "Market size" together renders CONFLICT only.
  4. A lead whose only selected reason is "Founder(s)" renders MANDATE, with
     no internal-only wording in the body.
  5. An Arabic-language applicant gets the Arabic template by default; the
     `language` override picks it explicitly; placeholders substitute
     correctly in both scripts.
  6. A missing first_name degrades to a usable greeting.
"""
from __future__ import annotations

from app.services import claude_agent, rejection_templates as rt

ARABIC_DESCRIPTION = (
    "شركة ناشئة تعمل في مجال التقنية العميقة في منطقة الشرق الأوسط وشمال أفريقيا، "
    "وتقدم حلولاً مبتكرة تعتمد على الذكاء الاصطناعي."
)
ENGLISH_DESCRIPTION = "A deep-tech startup building sensor hardware for logistics across the GCC."


def _lead(**overrides) -> dict:
    lead = {
        "company_name": "Acme Deep Tech",
        "founder_names": ["Jane Founder"],
        "description": ENGLISH_DESCRIPTION,
        "pitch_deck_text": "",
        "source_detail": "",
    }
    lead.update(overrides)
    return lead


# ---------------------------------------------------------------------------
# 1. zero LLM calls
# ---------------------------------------------------------------------------


def test_render_makes_no_llm_call(monkeypatch):
    def _fail_if_called(**_kwargs):
        raise AssertionError("_chat_completion must never be called by rejection_templates")

    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_called)

    for key in rt.TEMPLATE_PRECEDENCE:
        for lang in ("en", "ar"):
            rt.render_rejection_email(lead_data=_lead(), reasons=[], language=lang)
    # No exception means the choke point was never entered.


def test_rejection_templates_module_has_no_llm_client_dependency():
    # The whole point of "plain Python constants, no prompt and no model":
    # the module never touches the OpenAI client or _chat_completion.
    assert not hasattr(rt, "_chat_completion")
    assert not hasattr(rt, "_get_client")


# ---------------------------------------------------------------------------
# 2. byte-identical surrounding copy
# ---------------------------------------------------------------------------


def test_all_english_bodies_identical_outside_reason_sentence():
    lead = _lead()
    rendered = {key: rt.render_rejection_email(lead_data=lead, reasons=[key_reasons], language="en")
                for key, key_reasons in _ONE_REASON_PER_TEMPLATE_EN.items()}
    stripped = set()
    for key, result in rendered.items():
        sentence = rt._EN_REASON_SENTENCES[key]
        stripped.add(result["body"].replace(sentence, ""))
    assert len(stripped) == 1


def test_all_arabic_bodies_identical_outside_reason_sentence():
    lead = _lead()
    rendered = {key: rt.render_rejection_email(lead_data=lead, reasons=[key_reasons], language="ar")
                for key, key_reasons in _ONE_REASON_PER_TEMPLATE_EN.items()}
    stripped = set()
    for key, result in rendered.items():
        sentence = rt._AR_REASON_SENTENCES[key]
        stripped.add(result["body"].replace(sentence, ""))
    assert len(stripped) == 1


# One canonical reason label that maps to each template key, used by the two
# byte-identical-copy tests above.
_ONE_REASON_PER_TEMPLATE_EN = {
    rt.TEMPLATE_CONFLICT: "Conflict of interest",
    rt.TEMPLATE_MARKET_SIZE: "Market size",
    rt.TEMPLATE_TRACTION: "Lack of traction",
    rt.TEMPLATE_MANDATE: "Out of our stage",
}


def test_all_four_subjects_are_identical_per_language():
    lead = _lead()
    en_subjects = {
        rt.render_rejection_email(lead_data=lead, reasons=[r], language="en")["subject"]
        for r in _ONE_REASON_PER_TEMPLATE_EN.values()
    }
    ar_subjects = {
        rt.render_rejection_email(lead_data=lead, reasons=[r], language="ar")["subject"]
        for r in _ONE_REASON_PER_TEMPLATE_EN.values()
    }
    assert len(en_subjects) == 1
    assert len(ar_subjects) == 1


# ---------------------------------------------------------------------------
# 3. Conflict + Market size together -> CONFLICT only, once
# ---------------------------------------------------------------------------


def test_conflict_and_market_size_together_selects_conflict():
    assert rt.select_template(["Conflict of interest", "Market size"]) == rt.TEMPLATE_CONFLICT


def test_conflict_and_market_size_together_renders_conflict_sentence_only_once():
    result = rt.render_rejection_email(
        lead_data=_lead(), reasons=["Conflict of interest", "Market size"], language="en",
    )
    conflict_sentence = rt._EN_REASON_SENTENCES[rt.TEMPLATE_CONFLICT]
    market_sentence = rt._EN_REASON_SENTENCES[rt.TEMPLATE_MARKET_SIZE]
    assert result["body"].count(conflict_sentence) == 1
    assert market_sentence not in result["body"]
    assert result["template"] == rt.TEMPLATE_CONFLICT


def test_precedence_order_market_size_beats_traction_and_mandate():
    assert rt.select_template(["Market size", "Lack of traction", "Other"]) == rt.TEMPLATE_MARKET_SIZE


def test_precedence_order_traction_beats_mandate():
    assert rt.select_template(["Business Model", "Out of our region"]) == rt.TEMPLATE_TRACTION


# ---------------------------------------------------------------------------
# 4. INTERNAL_ONLY_REASONS -> MANDATE fallback, never in the body
# ---------------------------------------------------------------------------


def test_founder_only_reason_selects_mandate():
    assert rt.select_template(["Founder(s)"]) == rt.TEMPLATE_MANDATE


def test_founder_only_reason_renders_mandate_with_no_internal_wording():
    result = rt.render_rejection_email(lead_data=_lead(), reasons=["Founder(s)"], language="en")
    assert result["template"] == rt.TEMPLATE_MANDATE
    assert "founder" not in result["body"].lower()
    assert result["body"] == rt.render_rejection_email(
        lead_data=_lead(), reasons=[], language="en",
    )["body"]


def test_all_internal_only_reasons_alone_fall_back_to_mandate():
    for label in claude_agent.INTERNAL_ONLY_REASONS:
        assert rt.select_template([label]) == rt.TEMPLATE_MANDATE


def test_mixed_internal_and_shareable_reason_ignores_internal_one():
    assert rt.select_template(["Founder(s)", "Lack of traction"]) == rt.TEMPLATE_TRACTION
    result = rt.render_rejection_email(
        lead_data=_lead(), reasons=["Founder(s)", "Lack of traction"], language="en",
    )
    assert "founder" not in result["body"].lower()
    assert rt._EN_REASON_SENTENCES[rt.TEMPLATE_TRACTION] in result["body"]


# ---------------------------------------------------------------------------
# 5. language detection / override / placeholder substitution
# ---------------------------------------------------------------------------


def test_arabic_applicant_gets_arabic_template_by_default():
    lead = _lead(company_name="شركة التقنية", description=ARABIC_DESCRIPTION, pitch_deck_text=ARABIC_DESCRIPTION)
    result = rt.render_rejection_email(lead_data=lead, reasons=[])
    assert result["language"] == "ar"
    assert result["subject"] == rt._AR_SUBJECT


def test_english_applicant_gets_english_template_by_default():
    result = rt.render_rejection_email(lead_data=_lead(), reasons=[])
    assert result["language"] == "en"
    assert result["subject"] == rt._EN_SUBJECT


def test_language_toggle_overrides_detection():
    # English applicant, explicitly forced to Arabic.
    result = rt.render_rejection_email(lead_data=_lead(), reasons=[], language="ar")
    assert result["language"] == "ar"
    assert result["subject"] == rt._AR_SUBJECT

    # Arabic applicant, explicitly forced to English.
    lead = _lead(company_name="شركة التقنية", description=ARABIC_DESCRIPTION, pitch_deck_text=ARABIC_DESCRIPTION)
    result = rt.render_rejection_email(lead_data=lead, reasons=[], language="en")
    assert result["language"] == "en"
    assert result["subject"] == rt._EN_SUBJECT


def test_placeholders_substitute_correctly_in_english():
    result = rt.render_rejection_email(
        lead_data=_lead(company_name="Acme Deep Tech", founder_names=["Jane Founder"]),
        reasons=["Market size"],
        language="en",
    )
    assert "Hi Jane," in result["body"]
    assert "Acme Deep Tech" in result["body"]
    assert "Raed Ventures" in result["body"]


def test_placeholders_substitute_correctly_in_arabic():
    result = rt.render_rejection_email(
        lead_data=_lead(company_name="شركة التقنية", founder_names=["محمد"]),
        reasons=["Market size"],
        language="ar",
    )
    assert "مرحباً محمد،" in result["body"]
    assert "شركة التقنية" in result["body"]
    assert "رائد فنتشرز" in result["body"]


# ---------------------------------------------------------------------------
# 6. missing first_name degrades to a usable greeting
# ---------------------------------------------------------------------------


def test_missing_first_name_degrades_to_usable_english_greeting():
    result = rt.render_rejection_email(lead_data=_lead(founder_names=[]), reasons=[], language="en")
    assert "Hi ," not in result["body"]
    assert "Hi there," in result["body"]


def test_missing_first_name_degrades_to_usable_arabic_greeting():
    result = rt.render_rejection_email(lead_data=_lead(founder_names=None), reasons=[], language="ar")
    assert "مرحباً ،" not in result["body"]
    assert "مرحباً بكم،" in result["body"]


def test_blank_founder_name_entries_also_degrade():
    result = rt.render_rejection_email(
        lead_data=_lead(founder_names=["", "   "]), reasons=[], language="en",
    )
    assert "Hi there," in result["body"]


# ---------------------------------------------------------------------------
# 7. neither language invites the founder back (issue #232 appendix v4 --
#    this copy was reverted into the templates once already)
# ---------------------------------------------------------------------------


_NO_INVITE_BACK_PHRASES = ("تغيّرت", "مجدداً", "permanent no", "hear from you again")


def test_no_rendered_body_invites_the_founder_back():
    for key, reason in _ONE_REASON_PER_TEMPLATE_EN.items():
        for lang in ("en", "ar"):
            body = rt.render_rejection_email(lead_data=_lead(), reasons=[reason], language=lang)["body"]
            for phrase in _NO_INVITE_BACK_PHRASES:
                assert phrase not in body
