"""
Tests for the deckless-assessment judgment fix (issue #147): a missing pitch
deck must never by itself drive a REJECT. #144 lets deckless leads be scored
from website + description; this closes the follow-up bug where thin data was
effectively producing rejections.

Since the bucket is the model's holistic judgment (see
claude_agent._enforce_bucket_consistency -- no code-level data-gap override by
design), what we can verify deterministically is the CONTRACT with the model:

  1. The no-deck steering block (NO_DECK_GUIDANCE) is injected into the prompt
     only when there's genuinely no deck text, and tells the model exactly
     what the acceptance criteria require.
  2. The pipeline faithfully passes through whatever bucket/confidence/data_gaps
     the model returns -- so a MAYBE with populated data_gaps and reduced
     confidence surfaces untouched (no code path silently escalates it), and a
     REJECT backed by real disqualifying evidence also survives untouched (no
     code path suppresses a legitimate REJECT just because there was no deck).

Exercised against a fake DeepSeek client (mirrors the pattern in
test_rejection_email.py / test_owner_calendly_draft.py) so no live API key is
needed.
"""
from __future__ import annotations
import json
from types import SimpleNamespace

from app.services import claude_agent


class _CapturingCompletions:
    """Fake DeepSeek client: records the prompt it was called with and
    returns whatever payload the test configured."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))]
        )


class _CapturingClient:
    def __init__(self, payload: dict):
        self.completions = _CapturingCompletions(payload)
        self.chat = SimpleNamespace(completions=self.completions)


def _install_fake_llm(monkeypatch, payload: dict) -> _CapturingClient:
    client = _CapturingClient(payload)
    monkeypatch.setattr(claude_agent, "_get_client", lambda: client)
    return client


def _base_lead_data(**overrides) -> dict:
    lead_data = {
        "company_name": "Acme Deep Tech",
        "website": "https://acme.test",
        "description": "A deep-tech startup building sensor hardware.",
        "stage": "seed",
        "region": "MENA",
        "founder_names": ["Founder One"],
        "linkedin_urls": [],
        "pitch_deck_text": None,
    }
    lead_data.update(overrides)
    return lead_data


def _minimal_result(**overrides) -> dict:
    result = {
        "summary": "Thin-data synthesis.",
        "bucket": "MAYBE",
        "confidence_score": 50,
        "scoring_breakdown": {},
        "positive_signals": [],
        "red_flags": [],
        "data_gaps": [],
        "research_sources": [],
        "draft_type": None,
        "draft_subject": None,
        "draft_body": None,
    }
    result.update(overrides)
    return result


def test_no_deck_guidance_included_in_prompt_when_no_deck_text(monkeypatch):
    client = _install_fake_llm(monkeypatch, _minimal_result())
    claude_agent.assess_lead(_base_lead_data(pitch_deck_text=None), research_data={})

    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert "NO PITCH DECK IS AVAILABLE" in prompt
    assert "information gap, NOT a negative signal" in prompt
    assert "NEVER, by itself, justify a REJECT" in prompt
    assert "POSITIVE disqualifying evidence" in prompt
    assert "prefer" in prompt and "MAYBE over REJECT" in prompt
    assert "`data_gaps`" in prompt
    assert "`confidence_score`" in prompt


def test_no_deck_guidance_omitted_when_deck_text_present(monkeypatch):
    client = _install_fake_llm(monkeypatch, _minimal_result(bucket="YES", draft_type=None))
    claude_agent.assess_lead(
        _base_lead_data(pitch_deck_text="Real deck content about the product and team."),
        research_data={},
    )

    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert "NO PITCH DECK IS AVAILABLE" not in prompt


def test_no_deck_lead_with_no_disqualifying_evidence_can_return_maybe_with_reduced_confidence(monkeypatch):
    """A deckless lead the model can't confidently place should come back as
    MAYBE with data_gaps populated and a low confidence score -- not REJECT --
    and the pipeline must pass that through untouched."""
    payload = _minimal_result(
        bucket="MAYBE",
        confidence_score=35,
        data_gaps=["no pitch deck provided", "team background unverified", "traction unknown"],
    )
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(pitch_deck_text=None), research_data={})

    assert result["bucket"] == "MAYBE"
    assert result["confidence_score"] == 35
    assert result["data_gaps"] == [
        "no pitch deck provided",
        "team background unverified",
        "traction unknown",
    ]
    # MAYBE must never carry a draft.
    assert result["draft_type"] is None
    assert result["draft_subject"] is None
    assert result["draft_body"] is None


def test_no_deck_lead_with_disqualifying_evidence_can_still_be_rejected(monkeypatch):
    """A deckless lead with genuine positive disqualifying evidence (e.g.
    clearly out of region) must still be able to come back REJECT -- the fix
    for missing-data-driven rejections must not suppress a legitimate one."""
    payload = _minimal_result(
        bucket="REJECT",
        confidence_score=70,
        red_flags=["HQ and all customers are in Germany -- clearly outside MENA"],
        draft_type="rejection",
        draft_subject="Thanks for applying to Raed Ventures",
        draft_body=(
            "Hi there,\n\nThank you for applying to Raed Ventures. After review, this isn't "
            "a fit for us right now given our regional focus. We wish you well.\n\nBest,\n"
            "Raed Ventures"
        ),
    )
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(
        _base_lead_data(pitch_deck_text=None, region="Germany"), research_data={}
    )

    assert result["bucket"] == "REJECT"
    assert result["draft_type"] == "rejection"
    assert "outside MENA" in result["red_flags"][0]
