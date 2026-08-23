"""
Outreach drafts must reply in the applicant's own language -- Arabic-script
applications get an Arabic draft, everything else defaults to English
(issue #92). Language is detected deterministically (Arabic-script char
count vs Latin) from the applicant's ORIGINAL submission -- company name,
raw description, pitch deck text -- never from an AI-enriched field.

Covers:
  1. `detect_applicant_language` -- the pure deterministic gate.
  2. `assess_lead` / `regenerate_draft` -- the resolved instruction reaches
     the LLM prompt and (via a fake client that role-plays the model)
     produces an Arabic vs English subject+body, while the Calendly URL and
     owner name in the sign-off are preserved untranslated.
"""
from __future__ import annotations
import json
import re
from types import SimpleNamespace

from app.services import claude_agent

ARABIC_DESCRIPTION = (
    "شركة ناشئة تعمل على تطوير تقنيات الذكاء الاصطناعي لتحليل البيانات الطبية "
    "في منطقة الشرق الأوسط وشمال أفريقيا"
)
ENGLISH_DESCRIPTION = "A deep-tech startup building AI-driven medical imaging tools for MENA hospitals."


# ---------- 1. detect_applicant_language (pure, deterministic) ----------


def test_detects_arabic_from_description():
    lead_data = {"company_name": "شركة التقنية", "description": ARABIC_DESCRIPTION}
    assert claude_agent.detect_applicant_language(lead_data) == "ar"


def test_detects_english_from_description():
    lead_data = {"company_name": "Acme Deep Tech", "description": ENGLISH_DESCRIPTION}
    assert claude_agent.detect_applicant_language(lead_data) == "en"


def test_empty_submission_defaults_to_english():
    assert claude_agent.detect_applicant_language({}) == "en"
    assert claude_agent.detect_applicant_language({"company_name": "", "description": ""}) == "en"


def test_ambiguous_mixed_submission_defaults_to_english():
    # A stray Arabic word/name inside an otherwise-English submission should
    # not flip the whole email -- Arabic must clearly dominate.
    lead_data = {
        "company_name": "Acme Deep Tech",
        "description": "An English description mentioning a partner named شركة once.",
    }
    assert claude_agent.detect_applicant_language(lead_data) == "en"


def test_uses_pitch_deck_text_not_only_description():
    lead_data = {"company_name": "Acme", "description": "", "pitch_deck_text": ARABIC_DESCRIPTION * 3}
    assert claude_agent.detect_applicant_language(lead_data) == "ar"


# ---------- 2. assess_lead / regenerate_draft resolve + apply the instruction ----------


class _RoleplayingCompletions:
    """Fake DeepSeek client: reads the resolved language instruction out of
    the prompt it was given (mirrors _EchoingCompletions in
    test_owner_calendly_draft.py) and writes back a subject/body in that
    language, echoing the Calendly URL verbatim and signing off with a plain
    "Raed Ventures" (no individual name, issue #137) -- exactly as we
    instruct the real model to do."""

    def create(self, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        calendly_match = re.search(r"https://calendly\.com/\S+?(?=[.\s]|$)", prompt)
        calendly_url = calendly_match.group(0) if calendly_match else "MISSING_CALENDLY"
        is_arabic = "original submission is in ARABIC" in prompt

        if is_arabic:
            draft_subject = "طلبك للانضمام إلى راعد فنتشرز"
            draft_body = (
                f"مرحباً،\n\nشكراً لتقديم طلبكم إلى راعد فنتشرز. هل ترغبون في حجز مكالمة قصيرة؟ "
                f"{calendly_url}\n\nمع التحية،\nRaed Ventures"
            )
        else:
            draft_subject = "Thanks for applying to Raed Ventures"
            draft_body = (
                f"Hi there,\n\nWould you like to book a short call? {calendly_url}\n\n"
                "Best,\nRaed Ventures"
            )

        payload = {
            "summary": "Deep tech, MENA.",
            "bucket": "YES",
            "confidence_score": 85,
            "scoring_breakdown": {},
            "positive_signals": [],
            "red_flags": [],
            "data_gaps": [],
            "research_sources": [],
            "draft_type": "meeting_request",
            "draft_subject": draft_subject,
            "draft_body": draft_body,
        }
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


class _RoleplayingClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_RoleplayingCompletions())


def _install_fake_llm(monkeypatch):
    monkeypatch.setattr(claude_agent, "_get_client", lambda: _RoleplayingClient())


def _has_arabic(text: str) -> bool:
    return bool(claude_agent._ARABIC_CHAR_RE.search(text))


def test_assess_lead_arabic_applicant_yields_arabic_draft(monkeypatch):
    _install_fake_llm(monkeypatch)
    lead_data = {
        "company_name": "شركة التقنية",
        "website": "https://example.test",
        "description": ARABIC_DESCRIPTION,
        "stage": "seed",
        "region": "MENA",
        "founder_names": ["Founder One"],
        "linkedin_urls": [],
        "pitch_deck_text": ARABIC_DESCRIPTION,
    }
    result = claude_agent.assess_lead(
        lead_data,
        research_data={},
        owner_calendly="https://calendly.com/waleed-raed/pl",
        owner_name="Waleed",
    )
    assert _has_arabic(result["draft_subject"])
    assert _has_arabic(result["draft_body"])
    # Calendly URL is preserved verbatim (never translated/altered).
    assert "https://calendly.com/waleed-raed/pl" in result["draft_body"]
    # Sign-off is the generic "Raed Ventures" -- no individual name (issue #137).
    assert "Raed Ventures" in result["draft_body"]
    assert "Waleed" not in result["draft_body"]


def test_assess_lead_english_applicant_yields_english_draft(monkeypatch):
    _install_fake_llm(monkeypatch)
    lead_data = {
        "company_name": "Acme Deep Tech",
        "website": "https://acme.test",
        "description": ENGLISH_DESCRIPTION,
        "stage": "seed",
        "region": "MENA",
        "founder_names": ["Founder One"],
        "linkedin_urls": [],
        "pitch_deck_text": "deck text",
    }
    result = claude_agent.assess_lead(lead_data, research_data={})
    assert not _has_arabic(result["draft_subject"])
    assert not _has_arabic(result["draft_body"])


def test_assess_lead_ambiguous_submission_defaults_to_english_draft(monkeypatch):
    _install_fake_llm(monkeypatch)
    lead_data = {
        "company_name": "Acme Deep Tech",
        "website": "https://acme.test",
        "description": "",
        "stage": "seed",
        "region": "MENA",
        "founder_names": [],
        "linkedin_urls": [],
        "pitch_deck_text": "",
    }
    result = claude_agent.assess_lead(lead_data, research_data={})
    assert not _has_arabic(result["draft_subject"])
    assert not _has_arabic(result["draft_body"])


def test_regenerate_draft_arabic_applicant_yields_arabic_draft(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {
            "company_name": "شركة التقنية",
            "founder_names": ["Founder One"],
            "description": ARABIC_DESCRIPTION,
            "pitch_deck_text": "",
        },
        "YES",
        "promising team",
        owner_calendly="https://calendly.com/udayrvc/30min",
        owner_name="Uday",
    )
    assert _has_arabic(result["draft_subject"])
    assert _has_arabic(result["draft_body"])
    assert "https://calendly.com/udayrvc/30min" in result["draft_body"]
    assert "Raed Ventures" in result["draft_body"]
    assert "Uday" not in result["draft_body"]


def test_regenerate_draft_english_applicant_yields_english_draft(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {
            "company_name": "Acme Deep Tech",
            "founder_names": ["Founder One"],
            "description": ENGLISH_DESCRIPTION,
            "pitch_deck_text": "",
        },
        "YES",
        "promising team",
    )
    assert not _has_arabic(result["draft_subject"])
    assert not _has_arabic(result["draft_body"])


def test_regenerate_draft_missing_original_text_defaults_to_english(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "REJECT",
        "not a fit on stage",
    )
    assert not _has_arabic(result["draft_subject"])
    assert not _has_arabic(result["draft_body"])
