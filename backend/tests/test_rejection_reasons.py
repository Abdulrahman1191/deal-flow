"""
Tests for issue #223: the partner picks rejection reasons when regenerating a
REJECT draft, and the email reflects them -- except for reasons that are
judgements about the people involved (INTERNAL_ONLY_REASONS), which are
recorded but never written into a sentence the founder reads.

Covers:
  1. `claude_agent.INTERNAL_ONLY_REASONS` -- the named constant itself.
  2. `claude_agent.regenerate_draft(reasons=...)` -- the reason-aware clause,
     exercised against a fake DeepSeek client that role-plays a model
     complying with the prompt (mirrors _CapturingCompletions in
     test_signal_labels.py / _RoleplayingCompletions in test_draft_language.py).
  3. `claude_agent.resolve_unqualification_reason` -- a human selection
     outranks the AI generator, with no LLM call on that branch.
  4. POST /assessments/{lead_id}/regenerate-draft -- validates `reasons`
     (max 3, must be a canonical label), persists `rejection_reasons`, and
     end-to-end (real regenerate_draft + fake LLM) keeps internal-only
     reasons out of the email while still recording them.
  5. send/archive wiring -- Copper CF 244358 is populated from
     `rejection_reasons` when present, and from the AI generator when absent.
"""
from __future__ import annotations
import json
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services import claude_agent, copper_writer, email_sender
from app.services.auth import get_current_user

client = TestClient(app)

REENGAGEMENT_PHRASES = [
    "reach back out",
    "reconnect",
    "keep us posted",
    "if things change",
    "if things evolve",
]


# ---------------------------------------------------------------------------
# Fake DeepSeek client: reads the resolved reasons clause (if any) out of the
# prompt and writes it back as one factual clause in the rejection body --
# role-playing a model that complies with DRAFT_REGEN_USER_TEMPLATE's rules.
# ---------------------------------------------------------------------------

class _ReasonAwareCompletions:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][-1]["content"]
        is_arabic = "original submission is in ARABIC" in prompt
        match = re.search(r"actual reason\(s\) for passing: (.+?)\. Work this", prompt)
        reason_fragment = match.group(1) if match else None

        if is_arabic:
            subject = "بخصوص طلبكم إلى راعد فنتشرز"
            if reason_fragment:
                body = (
                    f"مرحباً،\n\nشكراً لتقديمكم إلى راعد فنتشرز. بعد المراجعة، هذا لا يناسب تركيزنا "
                    f"الحالي بسبب {reason_fragment}. نتمنى لكم التوفيق.\n\nمع التحية،\nRaed Ventures"
                )
            else:
                body = (
                    "مرحباً،\n\nشكراً لتقديمكم إلى راعد فنتشرز. بعد المراجعة، هذا لا يناسب تركيزنا "
                    "الحالي. نتمنى لكم التوفيق.\n\nمع التحية،\nRaed Ventures"
                )
        else:
            subject = "Thanks for applying to Raed Ventures"
            if reason_fragment:
                body = (
                    "Hi there,\n\nThank you for applying to Raed Ventures. After review, this isn't a "
                    f"fit for us right now given {reason_fragment}. We wish you well.\n\nBest,\nRaed Ventures"
                )
            else:
                body = (
                    "Hi there,\n\nThank you for applying to Raed Ventures. After review, this isn't a "
                    "fit for us right now given our stage focus. We wish you well.\n\nBest,\nRaed Ventures"
                )

        payload = {"draft_type": "rejection", "draft_subject": subject, "draft_body": body}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


class _ReasonAwareClient:
    def __init__(self):
        self.completions = _ReasonAwareCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def _install_fake_llm(monkeypatch) -> _ReasonAwareClient:
    fake_client = _ReasonAwareClient()
    monkeypatch.setattr(claude_agent, "_get_client", lambda: fake_client)
    return fake_client


def _has_arabic(text: str) -> bool:
    return bool(claude_agent._ARABIC_CHAR_RE.search(text))


# ---------------------------------------------------------------------------
# 1. INTERNAL_ONLY_REASONS
# ---------------------------------------------------------------------------

def test_internal_only_reasons_are_canonical_and_a_subset_of_options():
    assert claude_agent.INTERNAL_ONLY_REASONS == {
        "Founder(s)", "Dedication and focus", "Ownership structure",
    }
    assert claude_agent.INTERNAL_ONLY_REASONS <= set(claude_agent.UNQUAL_REASON_OPTIONS)


# ---------------------------------------------------------------------------
# 2. claude_agent.regenerate_draft(reasons=...)
# ---------------------------------------------------------------------------

def test_regenerate_draft_names_a_shareable_reason(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "REJECT",
        "not a fit",
        reasons=["Market size"],
    )
    assert result["draft_type"] == "rejection"
    assert "market size" in result["draft_body"].lower()
    assert len(result["draft_body"].split()) <= 70
    lowered = result["draft_body"].lower()
    for phrase in REENGAGEMENT_PHRASES:
        assert phrase not in lowered


def test_regenerate_draft_internal_only_reason_alone_is_generic(monkeypatch):
    fake_client = _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "REJECT",
        "not a fit",
        reasons=["Founder(s)"],
    )
    prompt = fake_client.completions.calls[0]["messages"][-1]["content"]
    assert "actual reason(s) for passing" not in prompt
    assert "founder" not in result["draft_body"].lower()


def test_regenerate_draft_mixed_reasons_writes_from_shareable_only(monkeypatch):
    fake_client = _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "REJECT",
        "not a fit",
        reasons=["Founder(s)", "Market size"],
    )
    prompt = fake_client.completions.calls[0]["messages"][-1]["content"]
    assert "Founder(s)" not in prompt
    assert "Market size" in prompt
    assert "market size" in result["draft_body"].lower()
    assert "founder" not in result["draft_body"].lower()


def test_regenerate_draft_yes_bucket_ignores_reasons(monkeypatch):
    fake_client = _install_fake_llm(monkeypatch)
    # YES uses a different draft_type in the fake client's payload only for
    # REJECT; here we just assert the reasons clause never reaches the
    # prompt at all for a non-REJECT bucket.
    claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "MAYBE",
        "ambiguous",
        reasons=["Market size"],
    )
    prompt = fake_client.completions.calls[0]["messages"][-1]["content"]
    assert "actual reason(s) for passing" not in prompt


def test_regenerate_draft_unknown_reason_is_silently_dropped(monkeypatch):
    fake_client = _install_fake_llm(monkeypatch)
    claude_agent.regenerate_draft(
        {"company_name": "Acme Deep Tech", "founder_names": ["Founder One"]},
        "REJECT",
        "not a fit",
        reasons=["Not a real reason"],
    )
    prompt = fake_client.completions.calls[0]["messages"][-1]["content"]
    assert "actual reason(s) for passing" not in prompt


def test_regenerate_draft_arabic_lead_with_reason_stays_arabic(monkeypatch):
    _install_fake_llm(monkeypatch)
    result = claude_agent.regenerate_draft(
        {
            "company_name": "شركة التقنية",
            "founder_names": ["Founder One"],
            "description": "شركة ناشئة تعمل في مجال التقنية في منطقة الشرق الأوسط وشمال أفريقيا",
            "pitch_deck_text": "",
        },
        "REJECT",
        "not a fit",
        reasons=["Market size"],
    )
    assert _has_arabic(result["draft_subject"])
    assert _has_arabic(result["draft_body"])
    assert "Raed Ventures" in result["draft_body"]


# ---------------------------------------------------------------------------
# 3. claude_agent.resolve_unqualification_reason
# ---------------------------------------------------------------------------

def test_resolve_unqualification_reason_prefers_human_selection_no_ai_call(monkeypatch):
    def _fail_if_called(**_kwargs):
        raise AssertionError("generate_unqualification_reason must not be called")

    monkeypatch.setattr(claude_agent, "generate_unqualification_reason", _fail_if_called)

    result = claude_agent.resolve_unqualification_reason(
        rejection_reasons=["Market size", "Founder(s)"],
        company_name="Acme",
        bucket="REJECT",
    )
    assert result == {"reason_option_ids": [367305, 1529401], "detail_text": None}


def test_resolve_unqualification_reason_falls_back_to_ai_when_no_human_selection(monkeypatch):
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {"reason_option_ids": [367302], "detail_text": "Lack of traction."},
    )
    result = claude_agent.resolve_unqualification_reason(
        rejection_reasons=None, company_name="Acme", bucket="REJECT",
    )
    assert result == {"reason_option_ids": [367302], "detail_text": "Lack of traction."}


def test_resolve_unqualification_reason_falls_back_on_empty_list(monkeypatch):
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {"reason_option_ids": [367302], "detail_text": "Lack of traction."},
    )
    result = claude_agent.resolve_unqualification_reason(
        rejection_reasons=[], company_name="Acme", bucket="REJECT",
    )
    assert result == {"reason_option_ids": [367302], "detail_text": "Lack of traction."}


# ---------------------------------------------------------------------------
# 4. POST /assessments/{lead_id}/regenerate-draft
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(results):
    session = _RecordingSession(results)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


def _fake_lead_row():
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email=None,
        company_name="Acme Deep Tech",
        founder_names=["Founder One"],
        description="An English-language description.",
        pitch_deck_text="deck text",
        raw_copper_data=None,
    )


def _fake_card_row(bucket="REJECT"):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket=bucket,
        confidence_score=60,
        summary="not a fit on stage",
        positive_signals=[],
        red_flags=[],
        data_gaps=[],
        scoring_breakdown={},
        draft_subject=None,
        draft_body=None,
        draft_type=None,
        research_sources=[],
        research_data={},
        assessed_without_deck=False,
        user_override=None,
        user_override_at=None,
        rejection_reasons=None,
        user_rating="up",
        user_rating_at=now,
        approved_at=None,
        sent_at=None,
        created_at=now,
    )


def test_regenerate_draft_endpoint_more_than_three_reasons_is_400_and_writes_nothing():
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/regenerate-draft",
            json={"reasons": ["Market size", "Business Model", "Exit potential", "Technology and IP"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None
    assert card.rejection_reasons is None


def test_regenerate_draft_endpoint_unknown_reason_is_400_and_writes_nothing(monkeypatch):
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    import app.routers.assessments as assessments_router

    def _fail_if_called(*_a, **_k):
        raise AssertionError("regenerate_draft must not be called when validation fails")

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fail_if_called)

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/regenerate-draft",
            json={"reasons": ["Not a real reason"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 400
    assert card.draft_body is None
    assert card.rejection_reasons is None


def test_regenerate_draft_endpoint_persists_rejection_reasons(monkeypatch):
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    captured = {}

    def _fake_regenerate(*_args, **kwargs):
        captured.update(kwargs)
        return {"draft_type": "rejection", "draft_subject": "Subject", "draft_body": "Body"}

    import app.routers.assessments as assessments_router

    monkeypatch.setattr(assessments_router.claude_agent, "regenerate_draft", _fake_regenerate)

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/regenerate-draft",
            json={"reasons": ["Market size"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert captured["reasons"] == ["Market size"]
    assert card.rejection_reasons == ["Market size"]
    assert response.json()["rejection_reasons"] == ["Market size"]


def test_regenerate_draft_endpoint_no_reasons_clears_prior_selection(monkeypatch):
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")
    card.rejection_reasons = ["Market size"]  # stale selection from a prior regen

    import app.routers.assessments as assessments_router

    monkeypatch.setattr(
        assessments_router.claude_agent,
        "regenerate_draft",
        lambda *_a, **_k: {"draft_type": "rejection", "draft_subject": "Subject", "draft_body": "Body"},
    )

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(f"/api/v1/assessments/{lead.id}/regenerate-draft")
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    assert card.rejection_reasons is None


def test_regenerate_draft_endpoint_founder_reason_alone_is_generic_but_recorded(monkeypatch):
    """End-to-end (real claude_agent.regenerate_draft + fake LLM, not a
    mocked regenerate_draft): the word 'founder' must never appear as a
    stated reason, yet rejection_reasons still records the selection."""
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _install_fake_llm(monkeypatch)

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/regenerate-draft",
            json={"reasons": ["Founder(s)"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert "founder" not in body["draft_body"].lower()
    assert body["rejection_reasons"] == ["Founder(s)"]
    assert card.rejection_reasons == ["Founder(s)"]


def test_regenerate_draft_endpoint_mixed_reasons_writes_from_shareable_only(monkeypatch):
    lead = _fake_lead_row()
    card = _fake_card_row(bucket="REJECT")

    _install_fake_llm(monkeypatch)

    _auth_as("owner@raed.vc")
    _use_db([(card, lead)])
    try:
        response = client.post(
            f"/api/v1/assessments/{lead.id}/regenerate-draft",
            json={"reasons": ["Founder(s)", "Market size"]},
        )
    finally:
        _clear_auth()
        _clear_db()

    assert response.status_code == 200
    body = response.json()
    assert "market size" in body["draft_body"].lower()
    assert "founder" not in body["draft_body"].lower()
    assert body["rejection_reasons"] == ["Founder(s)", "Market size"]


# ---------------------------------------------------------------------------
# 5. send/archive wiring: card.rejection_reasons drives Copper CF 244358
#    instead of the AI generator when present.
# ---------------------------------------------------------------------------

class _FinalizeSendSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _fake_send_card(rejection_reasons=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        bucket="REJECT",
        user_override=None,
        user_rating="up",
        confidence_score=60,
        summary="not a fit",
        red_flags=[],
        scoring_breakdown={},
        research_data={},
        draft_type="rejection",
        draft_subject="Thanks for applying",
        draft_body="Hi there, thank you for applying. Best, Raed Ventures",
        rejection_reasons=rejection_reasons,
        approved_at=None,
        sent_at=None,
    )


def _fake_send_lead(copper_id="98765"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_email="reviewer@raed.vc",
        copper_id=copper_id,
        copper_opportunity_id=None,
        copper_person_id=None,
        copper_company_id=None,
        company_name="Acme Deep Tech",
        founder_names=["Jane Founder"],
        raw_copper_data={"recipient_email": "founder@acme.test", "tags": ["existing-tag"]},
        pitch_deck_text=None,
        status="pending",
    )


def _configure_send(monkeypatch):
    monkeypatch.setattr(email_sender, "is_configured", lambda: True)
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **k: None)


def test_send_rejection_with_human_reasons_skips_ai_call(monkeypatch):
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)

    def _fail_if_called(**_kwargs):
        raise AssertionError("generate_unqualification_reason must not be called when a human picked reasons")

    monkeypatch.setattr(claude_agent, "generate_unqualification_reason", _fail_if_called)

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    card = _fake_send_card(rejection_reasons=["Market size"])
    lead = _fake_send_lead()

    async def _fake_user():
        return SimpleNamespace(email="reviewer@raed.vc", is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user

    async def _fake_get_db():
        yield _FinalizeSendSession([(card, lead), None])

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert calls == [("98765", ["existing-tag"], {"reason_option_ids": [367305], "detail_text": None})]


def test_send_rejection_without_human_reasons_falls_back_to_ai(monkeypatch):
    _configure_send(monkeypatch)
    monkeypatch.setattr(copper_writer, "mark_approved_in_copper", lambda *a, **k: None)
    monkeypatch.setattr(
        claude_agent,
        "generate_unqualification_reason",
        lambda **kwargs: {"reason_option_ids": [367302], "detail_text": "Lack of traction."},
    )

    calls = []
    monkeypatch.setattr(
        copper_writer,
        "archive_in_copper",
        lambda copper_id, existing_tags, **kwargs: calls.append((copper_id, existing_tags, kwargs)),
    )

    card = _fake_send_card(rejection_reasons=None)
    lead = _fake_send_lead()

    async def _fake_user():
        return SimpleNamespace(email="reviewer@raed.vc", is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user

    async def _fake_get_db():
        yield _FinalizeSendSession([(card, lead), None])

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        response = client.post(f"/api/v1/assessments/{card.lead_id}/send")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert calls == [("98765", ["existing-tag"], {"reason_option_ids": [367302], "detail_text": "Lack of traction."})]
