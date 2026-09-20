"""
Issue #168: `detect_applicant_language`'s only applicant-authored signals
besides `company_name`/`pitch_deck_text` were missing the Copper "Source
detail" custom field (id 244394 by default), which holds the original
inbound email subject -- often the applicant's own Arabic text even when
`lead.description` is an English AI/team-written summary.

Covers:
  1. `copper_service.get_custom_field_value` -- the generic CF-by-id reader,
     including the "missing/blank raw_copper_data" degrade-silently case.
  2. `app.tasks.assess_lead._run` -- builds `lead_data["source_detail"]` from
     the lead's `raw_copper_data` before calling `claude_agent.assess_lead`.
  3. `app.routers.assessments._regenerate_draft_for_bucket` -- same wiring for
     the manual-override draft-regeneration path, plus an end-to-end check
     that `_language_instruction` resolves to the Arabic instruction for an
     Arabic lead surfaced only through this signal.
"""
from __future__ import annotations
import asyncio
import uuid
from types import SimpleNamespace

from app.services import claude_agent, copper_service
from app.tasks import assess_lead

SOURCE_DETAIL_FIELD_ID = 244394
ARABIC_SUBJECT = "استفسار أهلية التقديم لمشروع قائم في مرحلة MVP"


# ---------- 1. copper_service.get_custom_field_value ----------


def test_get_custom_field_value_reads_matching_field():
    raw = {"custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": ARABIC_SUBJECT}]}
    assert copper_service.get_custom_field_value(raw, SOURCE_DETAIL_FIELD_ID) == ARABIC_SUBJECT


def test_get_custom_field_value_ignores_other_fields():
    raw = {"custom_fields": [{"custom_field_definition_id": 999, "value": "unrelated"}]}
    assert copper_service.get_custom_field_value(raw, SOURCE_DETAIL_FIELD_ID) == ""


def test_get_custom_field_value_missing_raw_copper_data_does_not_raise():
    assert copper_service.get_custom_field_value(None, SOURCE_DETAIL_FIELD_ID) == ""
    assert copper_service.get_custom_field_value({}, SOURCE_DETAIL_FIELD_ID) == ""
    assert copper_service.get_custom_field_value({"custom_fields": []}, SOURCE_DETAIL_FIELD_ID) == ""
    assert copper_service.get_custom_field_value({"custom_fields": None}, SOURCE_DETAIL_FIELD_ID) == ""


def test_get_custom_field_value_unset_field_id_is_noop():
    # field_id 0 (unconfigured) degrades silently, mirroring copper_cf_*_id=0
    # elsewhere in the app.
    raw = {"custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": ARABIC_SUBJECT}]}
    assert copper_service.get_custom_field_value(raw, 0) == ""


def test_get_custom_field_value_blank_value_is_empty_string():
    raw = {"custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": "   "}]}
    assert copper_service.get_custom_field_value(raw, SOURCE_DETAIL_FIELD_ID) == ""


# ---------- 2. app.tasks.assess_lead._run wiring ----------


class _FakeScalarResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeTaskSession:
    def __init__(self, lead):
        self.lead = lead

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, query):
        from app.models.lead import Lead
        from app.models.user import User

        entity = query.column_descriptions[0]["entity"]
        if entity is Lead:
            return _FakeScalarResult(self.lead)
        if entity is User:
            return _FakeScalarResult(None)
        return _FakeScalarResult(None)

    def add(self, obj):
        pass

    async def commit(self):
        pass


def _fake_lead_with_source_detail(raw_copper_data):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        pitch_deck_text="Deck contents go here " * 50,
        company_linkedin_url="https://linkedin.com/company/acme",
        company_name="Acme Deep Tech",
        website="https://acme.test",
        description="An English AI-written summary.",
        stage="seed",
        region="MENA",
        founder_names=["Founder One"],
        linkedin_urls=None,
        copper_id=None,
        raw_copper_data=raw_copper_data,
        owner_email=None,
    )


def test_assess_lead_task_extracts_source_detail_from_raw_copper_data(monkeypatch):
    raw_copper_data = {
        "custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": ARABIC_SUBJECT}]
    }
    lead = _fake_lead_with_source_detail(raw_copper_data)
    session = _FakeTaskSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})

    captured = {}

    def _fake_assess(lead_data, *_args, **_kwargs):
        captured["lead_data"] = lead_data
        return {
            "bucket": "YES",
            "confidence_score": 82,
            "summary": "Strong team.",
            "positive_signals": [],
            "red_flags": [],
            "data_gaps": [],
            "scoring_breakdown": {},
            "draft_subject": "Let's talk",
            "draft_body": "Hi there",
            "draft_type": "meeting_request",
            "research_sources": [],
            "precedents_cited": [],
        }

    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _fake_assess)

    import app.services.feedback_patterns as feedback_patterns

    async def _fake_exemplars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _fake_exemplars)

    asyncio.run(assess_lead._run(str(lead.id)))

    assert captured["lead_data"]["source_detail"] == ARABIC_SUBJECT


def test_assess_lead_task_source_detail_blank_when_raw_copper_data_missing(monkeypatch):
    lead = _fake_lead_with_source_detail(None)
    session = _FakeTaskSession(lead)
    monkeypatch.setattr(assess_lead, "CelerySessionLocal", lambda: session)
    monkeypatch.setattr(assess_lead.research, "research_company", lambda lead_data: {"sources": []})

    captured = {}

    def _fake_assess(lead_data, *_args, **_kwargs):
        captured["lead_data"] = lead_data
        return {
            "bucket": "YES",
            "confidence_score": 82,
            "summary": "Strong team.",
            "positive_signals": [],
            "red_flags": [],
            "data_gaps": [],
            "scoring_breakdown": {},
            "draft_subject": "Let's talk",
            "draft_body": "Hi there",
            "draft_type": "meeting_request",
            "research_sources": [],
            "precedents_cited": [],
        }

    monkeypatch.setattr(assess_lead.claude_agent, "assess_lead", _fake_assess)

    import app.services.feedback_patterns as feedback_patterns

    async def _fake_exemplars(*_args, **_kwargs):
        return []

    monkeypatch.setattr(feedback_patterns, "retrieve_labeled_exemplars", _fake_exemplars)

    # Must not raise despite raw_copper_data being None.
    asyncio.run(assess_lead._run(str(lead.id)))

    assert captured["lead_data"]["source_detail"] == ""


# ---------- 3. routers/assessments._regenerate_draft_for_bucket wiring ----------


def _fake_lead_row(raw_copper_data):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email=None,
        company_name="CleverDesign",
        founder_names=["Founder One"],
        description="An English AI-written summary of the applicant's product.",
        pitch_deck_text=None,
        raw_copper_data=raw_copper_data,
    )


def test_regenerate_draft_for_bucket_extracts_source_detail(monkeypatch):
    import app.routers.assessments as assessments_router

    raw_copper_data = {
        "custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": ARABIC_SUBJECT}]
    }
    lead = _fake_lead_row(raw_copper_data)

    captured = {}

    def _fake_regenerate(lead_data, *_args, **_kwargs):
        captured["lead_data"] = lead_data
        return {"draft_type": "rejection", "draft_subject": "Subject", "draft_body": "Body"}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    assessments_router._regenerate_draft_for_bucket(lead, "REJECT", "summary", {})

    assert captured["lead_data"]["source_detail"] == ARABIC_SUBJECT


def test_regenerate_draft_for_bucket_source_detail_blank_when_raw_copper_data_missing(monkeypatch):
    import app.routers.assessments as assessments_router

    lead = _fake_lead_row(None)

    captured = {}

    def _fake_regenerate(lead_data, *_args, **_kwargs):
        captured["lead_data"] = lead_data
        return {"draft_type": "rejection", "draft_subject": "Subject", "draft_body": "Body"}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    # Must not raise despite raw_copper_data being missing entirely.
    assessments_router._regenerate_draft_for_bucket(lead, "REJECT", "summary", {})

    assert captured["lead_data"]["source_detail"] == ""


def test_language_instruction_resolves_arabic_for_regenerated_draft_via_source_detail(monkeypatch):
    """End-to-end: a lead with a Latin company name and English enrichment
    note, but an Arabic Copper "Source detail" subject, must still resolve to
    the Arabic language instruction when its draft is regenerated."""
    import app.routers.assessments as assessments_router

    raw_copper_data = {
        "custom_fields": [{"custom_field_definition_id": SOURCE_DETAIL_FIELD_ID, "value": ARABIC_SUBJECT}]
    }
    lead = _fake_lead_row(raw_copper_data)

    captured = {}

    def _fake_regenerate(lead_data, *_args, **_kwargs):
        captured["lead_data"] = lead_data
        return {"draft_type": "rejection", "draft_subject": "Subject", "draft_body": "Body"}

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    assessments_router._regenerate_draft_for_bucket(lead, "REJECT", "summary", {})

    instruction = claude_agent._language_instruction(captured["lead_data"])
    assert instruction == claude_agent._LANGUAGE_INSTRUCTIONS["ar"]
