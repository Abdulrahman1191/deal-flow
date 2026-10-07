"""
Tests for issue #221: the assessor emits a structured `traction` array --
at most 4 short, evidenced hard metrics -- instead of leaving them buried
inside a signal sentence.

Covers:
  1. `claude_agent.normalize_traction_list` -- the shared reader/normaliser
     used both right after the model responds and as the API serializer's
     backstop.
  2. `claude_agent._enforce_bucket_consistency` -- trims/drops malformed
     entries before anything is persisted, never raises.
  3. `AssessmentOut` -- an absent/null `traction` serializes as `[]`, never
     as `None` or a raise.

Mirrors the fake-DeepSeek-client pattern from test_signal_labels.py so no
live API key is needed.
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import claude_agent
from app.services.claude_agent import normalize_traction_list
from app.schemas.assessment import AssessmentOut


# ---------------------------------------------------------------------------
# normalize_traction_list
# ---------------------------------------------------------------------------

def test_normalize_traction_list_passes_through_well_formed_items():
    assert normalize_traction_list(["$220K GMV", "550 vendors"]) == ["$220K GMV", "550 vendors"]


def test_normalize_traction_list_trims_whitespace():
    assert normalize_traction_list(["  $220K GMV  "]) == ["$220K GMV"]


def test_normalize_traction_list_trims_to_four_items():
    items = ["$220K GMV", "550 vendors", "8 paid stores", "3,000+ users", "a 5th metric"]
    result = normalize_traction_list(items)
    assert result == ["$220K GMV", "550 vendors", "8 paid stores", "3,000+ users"]
    assert len(result) == 4


def test_normalize_traction_list_drops_empty_and_malformed_entries_without_raising():
    assert normalize_traction_list(["$220K GMV", "", "   ", None, 42, {"a": "b"}]) == ["$220K GMV"]


def test_normalize_traction_list_handles_none_and_empty():
    assert normalize_traction_list(None) == []
    assert normalize_traction_list([]) == []


# ---------------------------------------------------------------------------
# _enforce_bucket_consistency normalises traction in place
# ---------------------------------------------------------------------------

class _CapturingCompletions:
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
        "pitch_deck_text": "Real deck content about the product and team.",
    }
    lead_data.update(overrides)
    return lead_data


def _minimal_result(**overrides) -> dict:
    result = {
        "summary": "Thin-data synthesis.",
        "bucket": "YES",
        "confidence_score": 80,
        "scoring_breakdown": {},
        "positive_signals": [],
        "red_flags": [],
        "traction": [],
        "data_gaps": [],
        "research_sources": [],
        "draft_type": "meeting_request",
        "draft_subject": "s",
        "draft_body": "b",
    }
    result.update(overrides)
    return result


def test_assess_lead_yields_separate_traction_entries_for_evidenced_metrics(monkeypatch):
    payload = _minimal_result(traction=["$220K GMV", "550 vendors"])
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["traction"] == ["$220K GMV", "550 vendors"]


def test_assess_lead_traction_empty_when_only_market_size_and_raise_are_present(monkeypatch):
    # The model is instructed never to put market-size/raise figures into
    # `traction` -- this is the contract test: whatever the model returns in
    # `traction` passes through untouched (code never infers from other
    # fields), so a lead whose only numbers are market/ask context should
    # have the model return an empty array.
    payload = _minimal_result(traction=[])
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["traction"] == []


def test_assess_lead_trims_traction_over_four_items(monkeypatch):
    payload = _minimal_result(
        traction=["$220K GMV", "550 vendors", "8 paid stores", "3,000+ users", "a 5th metric"]
    )
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["traction"] == ["$220K GMV", "550 vendors", "8 paid stores", "3,000+ users"]


def test_assess_lead_drops_malformed_traction_entries_without_raising(monkeypatch):
    payload = _minimal_result(traction=[None, 42, "", "  ", "$5M raise"])
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["traction"] == ["$5M raise"]


def test_assess_lead_missing_traction_key_normalises_to_empty_list(monkeypatch):
    payload = _minimal_result()
    del payload["traction"]
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["traction"] == []


# ---------------------------------------------------------------------------
# AssessmentOut -- API serializer
# ---------------------------------------------------------------------------

def _assessment_out_base(**overrides) -> dict:
    base = dict(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket="YES",
        confidence_score=80,
        summary="s",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject=None,
        draft_body=None,
        draft_type=None,
        research_sources=[],
        assessed_without_deck=False,
        user_override=None,
        user_override_at=None,
        user_rating=None,
        user_rating_at=None,
        approved_at=None,
        sent_at=None,
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return base


def test_assessment_out_traction_absent_field_serializes_as_empty_list():
    out = AssessmentOut(**_assessment_out_base())
    assert out.traction == []


def test_assessment_out_traction_none_serializes_as_empty_list():
    out = AssessmentOut(**_assessment_out_base(traction=None))
    assert out.traction == []


def test_assessment_out_exposes_evidenced_traction():
    out = AssessmentOut(**_assessment_out_base(traction=["$220K GMV", "550 vendors"]))
    assert out.traction == ["$220K GMV", "550 vendors"]


def test_assessment_out_trims_and_sanitizes_traction_at_the_serializer_too():
    out = AssessmentOut(
        **_assessment_out_base(
            traction=["$220K GMV", "550 vendors", "8 paid stores", "3,000+ users", "a 5th", None, 42]
        )
    )
    assert out.traction == ["$220K GMV", "550 vendors", "8 paid stores", "3,000+ users"]
