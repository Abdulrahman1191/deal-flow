"""
Tests for issue #200: the assessor emits an explicit short `label` alongside
each `positive_signals` / `red_flags` entry, instead of the frontend having
to cut one out of a sentence with a regex.

Covers:
  1. `claude_agent.normalize_signal` -- the one shared reader every consumer
     (API serializer, exports, eval scripts) should read a signal through.
  2. `claude_agel._enforce_bucket_consistency` -- trims/drops a malformed
     label right after the model responds, before anything is persisted.
  3. `AssessmentOut` -- the API serializer accepts both shapes forever and
     never regresses the ~1,000 existing plain-string cards.
  4. `generate_unqualification_reason` -- reads labelled and unlabelled
     red_flags identically (the only place in the backend that currently
     reads signal *text* back out, beyond passthrough).

Mirrors the fake-DeepSeek-client pattern from test_no_deck_assessment_prompt.py
so no live API key is needed.
"""
from __future__ import annotations
import json
from types import SimpleNamespace

from app.services import claude_agent
from app.services.claude_agent import normalize_signal, _normalize_signal_list
from app.schemas.assessment import AssessmentOut
import uuid
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# normalize_signal
# ---------------------------------------------------------------------------

def test_normalize_signal_plain_string_has_no_label():
    label, text = normalize_signal("KAUST-backed founding team")
    assert label is None
    assert text == "KAUST-backed founding team"


def test_normalize_signal_object_exposes_label_and_text():
    label, text = normalize_signal(
        {"label": "Founder identity unverified", "text": "The CRM contact's title could not be confirmed."}
    )
    assert label == "Founder identity unverified"
    assert text == "The CRM contact's title could not be confirmed."


def test_normalize_signal_trims_label_over_six_words():
    label, text = normalize_signal(
        {"label": "This label has way more than six words in it", "text": "Explanation."}
    )
    assert label == "This label has way more than"
    assert len(label.split()) == 6
    assert text == "Explanation."


def test_normalize_signal_empty_label_falls_back_to_text():
    label, text = normalize_signal({"label": "   ", "text": "Just the text."})
    assert label is None
    assert text == "Just the text."


def test_normalize_signal_missing_label_falls_back_to_text():
    label, text = normalize_signal({"text": "No label key at all."})
    assert label is None
    assert text == "No label key at all."


def test_normalize_signal_malformed_item_does_not_raise():
    # None, a bare number, a non-string label, a dict with no usable text at
    # all -- none of these may raise; every one degrades to best-effort text.
    assert normalize_signal(None) == (None, "")
    assert normalize_signal(42) == (None, "42")
    assert normalize_signal({"label": 99, "text": "ok"}) == (None, "ok")
    label, text = normalize_signal({"label": "Some Label"})  # no "text" key
    assert label == "Some Label"
    assert text == "Some Label"  # falls back to the label itself rather than ""


def test_normalize_signal_list_round_trips_well_formed_items():
    items = [
        "a plain string signal",
        {"label": "Short label", "text": "Some text"},
        {"label": "", "text": "Empty label falls back to plain string"},
    ]
    assert _normalize_signal_list(items) == [
        "a plain string signal",
        {"label": "Short label", "text": "Some text"},
        "Empty label falls back to plain string",
    ]


def test_normalize_signal_list_handles_none_and_empty():
    assert _normalize_signal_list(None) == []
    assert _normalize_signal_list([]) == []


# ---------------------------------------------------------------------------
# _enforce_bucket_consistency normalises positive_signals/red_flags in place
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
        "data_gaps": [],
        "research_sources": [],
        "draft_type": "meeting_request",
        "draft_subject": "s",
        "draft_body": "b",
    }
    result.update(overrides)
    return result


def test_assess_lead_passes_through_plain_string_signals_unchanged(monkeypatch):
    payload = _minimal_result(
        positive_signals=["KAUST-backed", "Patent-protected hardware"],
        red_flags=["Single-country exposure"],
    )
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["positive_signals"] == ["KAUST-backed", "Patent-protected hardware"]
    assert result["red_flags"] == ["Single-country exposure"]


def test_assess_lead_trims_oversized_label_and_drops_empty_one(monkeypatch):
    payload = _minimal_result(
        positive_signals=[
            {"label": "Moat unevidenced", "text": "No patents, no proprietary data found."},
        ],
        red_flags=[
            {"label": "This label has way more than six words", "text": "Too long, must be trimmed."},
            {"label": "", "text": "Empty label must fall back to the plain-string form."},
            "a plain string red flag stays untouched",
        ],
    )
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["positive_signals"] == [
        {"label": "Moat unevidenced", "text": "No patents, no proprietary data found."}
    ]
    assert result["red_flags"][0]["label"].split().__len__() == 6
    assert result["red_flags"][0]["text"] == "Too long, must be trimmed."
    assert result["red_flags"][1] == "Empty label must fall back to the plain-string form."
    assert result["red_flags"][2] == "a plain string red flag stays untouched"


def test_assess_lead_malformed_signal_item_does_not_raise(monkeypatch):
    payload = _minimal_result(positive_signals=[None, 42, {"label": 7}], red_flags=[])
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.assess_lead(_base_lead_data(), research_data={})

    assert result["positive_signals"] == ["", "42", ""]


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


def test_assessment_out_renders_plain_strings_exactly_as_today():
    out = AssessmentOut(
        **_assessment_out_base(positive_signals=["KAUST-backed"], red_flags=["Single-country exposure"])
    )
    assert out.positive_signals == ["KAUST-backed"]
    assert out.red_flags == ["Single-country exposure"]


def test_assessment_out_exposes_label_and_text_for_object_items():
    out = AssessmentOut(
        **_assessment_out_base(
            positive_signals=[{"label": "Moat unevidenced", "text": "No patents found."}],
            red_flags=[],
        )
    )
    assert out.positive_signals == [{"label": "Moat unevidenced", "text": "No patents found."}]


def test_assessment_out_trims_oversized_label_at_the_serializer_too():
    out = AssessmentOut(
        **_assessment_out_base(
            positive_signals=[{"label": "x y z a b c d e", "text": "too long label"}],
            red_flags=[],
        )
    )
    assert out.positive_signals == [{"label": "x y z a b c", "text": "too long label"}]


def test_assessment_out_malformed_item_does_not_raise():
    out = AssessmentOut(
        **_assessment_out_base(positive_signals=[123, None, {"bad": "shape"}], red_flags=None)
    )
    assert out.positive_signals == ["123", "", ""]
    assert out.red_flags is None


# ---------------------------------------------------------------------------
# generate_unqualification_reason -- reads labelled/unlabelled red_flags
# identically through the shared helper (the eval/export-style consumer).
# ---------------------------------------------------------------------------

def test_generate_unqualification_reason_reads_plain_and_labelled_red_flags_identically(monkeypatch):
    payload = {"reasons": ["Market size"], "detail": "Out of our stage."}

    client_plain = _install_fake_llm(monkeypatch, payload)
    claude_agent.generate_unqualification_reason(
        company_name="Acme", bucket="REJECT", summary="s",
        red_flags=["Single-country exposure"],
    )
    prompt_plain = client_plain.completions.calls[0]["messages"][1]["content"]

    client_labelled = _install_fake_llm(monkeypatch, payload)
    claude_agent.generate_unqualification_reason(
        company_name="Acme", bucket="REJECT", summary="s",
        red_flags=[{"label": "Single market", "text": "Single-country exposure"}],
    )
    prompt_labelled = client_labelled.completions.calls[0]["messages"][1]["content"]

    assert prompt_plain == prompt_labelled
    assert "Single-country exposure" in prompt_plain


def test_generate_unqualification_reason_does_not_raise_on_mixed_shapes(monkeypatch):
    payload = {"reasons": ["Market size"], "detail": "Out of our stage."}
    _install_fake_llm(monkeypatch, payload)

    result = claude_agent.generate_unqualification_reason(
        company_name="Acme",
        bucket="REJECT",
        summary="s",
        red_flags=["plain flag", {"label": "Instrument mismatch", "text": "SAFE vs equity mismatch."}],
    )

    assert result["detail_text"] == "Out of our stage."
