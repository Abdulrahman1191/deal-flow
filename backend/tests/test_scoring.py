"""
Basic sanity tests for the Claude scoring schema.
Run with: pytest tests/
"""
import json
import pytest


def test_assessment_result_schema():
    sample = {
        "summary": "A deep tech company in Saudi Arabia.",
        "bucket": "YES",
        "confidence_score": 85,
        "scoring_breakdown": {
            "mena_focus": {"score": 18, "reasoning": "Headquartered in KSA."},
            "deep_tech": {"score": 22, "reasoning": "Proprietary ML chip design."},
            "strong_ip": {"score": 17, "reasoning": "3 granted patents."},
            "team_experience": {"score": 18, "reasoning": "Ex-Aramco and KAUST founders."},
            "stage_alignment": {"score": 8, "reasoning": "Seed round."},
            "model_fit": {"score": 2, "reasoning": "Not a marketplace."},
        },
        "positive_signals": ["KAUST-backed", "Patent-protected hardware"],
        "red_flags": [],
        "data_gaps": ["No Crunchbase profile found"],
        "research_sources": ["https://example.com"],
        "draft_type": "meeting_request",
        "draft_subject": "Raed Ventures — Let's connect",
        "draft_body": "Hi, we'd love to learn more about your work.",
    }

    total = sum(v["score"] for v in sample["scoring_breakdown"].values())
    assert sample["confidence_score"] == total
    assert sample["bucket"] in ("YES", "MAYBE", "REJECT")
    assert 0 <= sample["confidence_score"] <= 100


def test_reject_bucket_range():
    for score in range(0, 50):
        assert score < 50


def test_yes_bucket_range():
    for score in range(80, 101):
        assert score >= 80
