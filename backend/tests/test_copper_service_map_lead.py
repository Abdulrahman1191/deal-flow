"""
Tests for map_copper_lead's stage selection (issue #93): the first Copper tag
was being stored verbatim as `stage`, which is often one of our own internal
raed:* tags (raed:bucket:maybe, raed:override, raed:archived, ...) written by
copper_writer.py. Those must never be picked as the displayed stage.
"""
from app.services.copper_service import map_copper_lead


def _lead(tags):
    return {"id": 1, "name": "Founder", "tags": tags}


def test_stage_skips_leading_raed_tag():
    mapped = map_copper_lead(_lead(["raed:bucket:maybe", "Seed"]))
    assert mapped["stage"] == "Seed"


def test_stage_none_when_only_raed_tags():
    mapped = map_copper_lead(_lead(["raed:bucket:yes", "raed:override"]))
    assert mapped["stage"] is None


def test_stage_none_when_no_tags():
    mapped = map_copper_lead(_lead([]))
    assert mapped["stage"] is None


def test_stage_uses_first_genuine_tag_when_already_first():
    mapped = map_copper_lead(_lead(["Series A", "raed:archived"]))
    assert mapped["stage"] == "Series A"
