"""
Tests for scripts/data_quality_report.py's classifier helpers.

Pure unit tests against synthetic lead/card rows (SimpleNamespace) -- no live
DB, mirrors the pattern in test_csv_export.py / test_run_pitch_sync.py.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import data_quality_report as dqr  # noqa: E402


def make_lead(pitch_deck_text=None, pitch_deck_drive_id=None, company_name="Acme"):
    return SimpleNamespace(
        company_name=company_name,
        pitch_deck_text=pitch_deck_text,
        pitch_deck_drive_id=pitch_deck_drive_id,
    )


def make_card(bucket="REJECT", user_override=None, confidence_score=80, data_gaps=None):
    return SimpleNamespace(
        bucket=bucket,
        user_override=user_override,
        confidence_score=confidence_score,
        data_gaps=data_gaps,
    )


# --- has_pitch_deck / is_missing_pitch_deck ---------------------------------

def test_has_pitch_deck_true_with_text():
    lead = make_lead(pitch_deck_text="Some extracted text")
    assert dqr.has_pitch_deck(lead) is True
    assert dqr.is_missing_pitch_deck(lead) is False


def test_has_pitch_deck_true_with_drive_id_only():
    lead = make_lead(pitch_deck_drive_id="abc123")
    assert dqr.has_pitch_deck(lead) is True
    assert dqr.is_missing_pitch_deck(lead) is False


def test_has_pitch_deck_false_when_both_null():
    lead = make_lead()
    assert dqr.has_pitch_deck(lead) is False
    assert dqr.is_missing_pitch_deck(lead) is True


def test_has_pitch_deck_false_when_text_is_empty_string():
    lead = make_lead(pitch_deck_text="")
    assert dqr.is_missing_pitch_deck(lead) is True


# --- is_half_baked_reject ----------------------------------------------------

def test_half_baked_false_when_no_card():
    lead = make_lead(pitch_deck_text="text")
    assert dqr.is_half_baked_reject(lead, None) is False


def test_half_baked_false_when_bucket_is_not_reject():
    lead = make_lead()  # no deck, would be thin, but bucket isn't REJECT
    card = make_card(bucket="MAYBE", confidence_score=20, data_gaps=["missing website"])
    assert dqr.is_half_baked_reject(lead, card) is False


def test_half_baked_false_reject_with_strong_evidence():
    """REJECT with high confidence, no data gaps, and a deck -- genuine poor fit."""
    lead = make_lead(pitch_deck_text="Full deck text")
    card = make_card(bucket="REJECT", confidence_score=90, data_gaps=[])
    assert dqr.is_half_baked_reject(lead, card) is False


def test_half_baked_true_low_confidence():
    lead = make_lead(pitch_deck_text="Full deck text")
    card = make_card(bucket="REJECT", confidence_score=49, data_gaps=[])
    assert dqr.is_half_baked_reject(lead, card) is True


def test_half_baked_false_at_confidence_threshold_boundary():
    """confidence_score == threshold is NOT below it -- must not flag."""
    lead = make_lead(pitch_deck_text="Full deck text")
    card = make_card(bucket="REJECT", confidence_score=dqr.CONFIDENCE_THRESHOLD, data_gaps=[])
    assert dqr.is_half_baked_reject(lead, card) is False


def test_half_baked_true_nonempty_data_gaps():
    lead = make_lead(pitch_deck_text="Full deck text")
    card = make_card(bucket="REJECT", confidence_score=90, data_gaps=["No LinkedIn found"])
    assert dqr.is_half_baked_reject(lead, card) is True


def test_half_baked_false_empty_data_gaps_list():
    lead = make_lead(pitch_deck_text="Full deck text")
    card = make_card(bucket="REJECT", confidence_score=90, data_gaps=[])
    assert dqr.is_half_baked_reject(lead, card) is False


def test_half_baked_false_when_data_gaps_is_none():
    lead = make_lead(pitch_deck_text="Full deck text")
    card = make_card(bucket="REJECT", confidence_score=90, data_gaps=None)
    assert dqr.is_half_baked_reject(lead, card) is False


def test_half_baked_true_missing_deck():
    lead = make_lead()  # no deck
    card = make_card(bucket="REJECT", confidence_score=90, data_gaps=[])
    assert dqr.is_half_baked_reject(lead, card) is True


def test_half_baked_respects_user_override_to_reject():
    """AI bucket was YES, but a human overrode it to REJECT -- effective bucket wins."""
    lead = make_lead()  # no deck
    card = make_card(bucket="YES", user_override="REJECT", confidence_score=90, data_gaps=[])
    assert dqr.is_half_baked_reject(lead, card) is True


def test_half_baked_respects_user_override_away_from_reject():
    """AI bucket was REJECT (and thin), but a human overrode it to MAYBE -- not half-baked anymore."""
    lead = make_lead()  # no deck
    card = make_card(bucket="REJECT", user_override="MAYBE", confidence_score=10, data_gaps=["gap"])
    assert dqr.is_half_baked_reject(lead, card) is False


# --- build_report grouping ----------------------------------------------------

def test_build_report_groups_by_owner_and_computes_overall():
    rows = [
        (SimpleNamespace(company_name="A", owner_email="alice@raed.vc",
                          pitch_deck_text=None, pitch_deck_drive_id=None),
         make_card(bucket="REJECT", confidence_score=10, data_gaps=["x"])),
        (SimpleNamespace(company_name="B", owner_email="alice@raed.vc",
                          pitch_deck_text="text", pitch_deck_drive_id=None),
         make_card(bucket="YES", confidence_score=90, data_gaps=[])),
        (SimpleNamespace(company_name="C", owner_email="bob@raed.vc",
                          pitch_deck_text=None, pitch_deck_drive_id=None),
         None),
    ]
    overall, by_owner = dqr.build_report(rows)

    assert overall.total == 3
    assert overall.missing_deck_count == 2
    assert overall.half_baked_count == 1

    assert by_owner["alice@raed.vc"].total == 2
    assert by_owner["alice@raed.vc"].missing_deck_count == 1
    assert by_owner["alice@raed.vc"].half_baked_count == 1

    assert by_owner["bob@raed.vc"].total == 1
    assert by_owner["bob@raed.vc"].missing_deck_count == 1
    assert by_owner["bob@raed.vc"].half_baked_count == 0


def test_build_report_groups_unassigned_owner():
    rows = [
        (SimpleNamespace(company_name="A", owner_email=None,
                          pitch_deck_text="text", pitch_deck_drive_id=None),
         None),
    ]
    overall, by_owner = dqr.build_report(rows)
    assert dqr.UNASSIGNED_OWNER in by_owner
    assert by_owner[dqr.UNASSIGNED_OWNER].total == 1
