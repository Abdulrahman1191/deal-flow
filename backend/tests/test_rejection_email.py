"""
Rejection-draft signature and closing content (issue #137):

  1. The signature must read a plain "Raed Ventures" -- no individual
     associate/owner name -- since outreach now sends from the unified
     submission@raed.vc address.
  2. The closing must NOT invite the founder to reach back out / reconnect /
     keep us posted if things change. A pass should be polite and final.

Exercised against a fake DeepSeek client (mirrors the LLM-mocking pattern in
test_owner_calendly_draft.py) that role-plays a model complying with the
current REJECT-bucket prompt instructions, so no live API key is needed.
"""
from __future__ import annotations
import json
from types import SimpleNamespace

from app.services import claude_agent

REENGAGEMENT_PHRASES = [
    "reach back out",
    "reach out if things",
    "reconnect",
    "keep us posted",
    "if things change",
    "if things evolve",
]


class _RejectionCompletions:
    """Fake DeepSeek client: always returns a REJECT-bucket draft that
    complies with the current prompt -- generic "Raed Ventures" sign-off,
    no invitation to reconnect."""

    def create(self, **kwargs):
        payload = {
            "summary": "Not a fit on stage.",
            "bucket": "REJECT",
            "confidence_score": 60,
            "scoring_breakdown": {},
            "positive_signals": [],
            "red_flags": [],
            "data_gaps": [],
            "research_sources": [],
            "draft_type": "rejection",
            "draft_subject": "Thanks for applying to Raed Ventures",
            "draft_body": (
                "Hi there,\n\nThank you for applying to Raed Ventures. After review, this "
                "isn't a fit for us right now given our current stage focus. We wish you "
                "well with the company.\n\nBest,\nRaed Ventures"
            ),
        }
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


class _RejectionClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_RejectionCompletions())


def _install_fake_llm(monkeypatch):
    monkeypatch.setattr(claude_agent, "_get_client", lambda: _RejectionClient())


def test_assess_lead_rejection_signature_has_no_individual_name(monkeypatch):
    _install_fake_llm(monkeypatch)
    lead_data = {
        "company_name": "Acme Deep Tech",
        "website": "https://acme.test",
        "description": "A deep-tech startup.",
        "stage": "series-c",
        "region": "MENA",
        "founder_names": ["Founder One"],
        "linkedin_urls": [],
        "pitch_deck_text": "deck text",
    }
    result = claude_agent.assess_lead(
        lead_data,
        research_data={},
        owner_calendly="https://calendly.com/waleed-raed/pl",
        owner_name="Waleed",
    )
    assert result["draft_type"] == "rejection"
    assert "Raed Ventures" in result["draft_body"]
    assert "Waleed" not in result["draft_body"]


def test_assess_lead_rejection_has_no_reengagement_invitation(monkeypatch):
    _install_fake_llm(monkeypatch)
    lead_data = {
        "company_name": "Acme Deep Tech",
        "website": "https://acme.test",
        "description": "A deep-tech startup.",
        "stage": "series-c",
        "region": "MENA",
        "founder_names": ["Founder One"],
        "linkedin_urls": [],
        "pitch_deck_text": "deck text",
    }
    result = claude_agent.assess_lead(lead_data, research_data={})
    lowered = result["draft_body"].lower()
    for phrase in REENGAGEMENT_PHRASES:
        assert phrase not in lowered


def test_regenerate_draft_rejection_signature_has_no_individual_name(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "REJECT",
        "not a fit on stage",
        owner_calendly="https://calendly.com/udayrvc/30min",
        owner_name="Uday",
    )
    assert result["draft_type"] == "rejection"
    assert "Raed Ventures" in result["draft_body"]
    assert "Uday" not in result["draft_body"]


def test_regenerate_draft_rejection_has_no_reengagement_invitation(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "REJECT",
        "not a fit on stage",
    )
    lowered = result["draft_body"].lower()
    for phrase in REENGAGEMENT_PHRASES:
        assert phrase not in lowered
