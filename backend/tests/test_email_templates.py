"""
Tests for issue #234: rejection email templates move out of the #232 Python
constants into the `email_templates` / `email_template_versions` tables, so
copy can be edited without a deploy and a change reaches every lead --
including ones assessed months ago -- with no backfill.

Same fake-session + TestClient pattern as test_rejection_templates.py /
test_undo_archive.py -- no live Postgres in this CI, so app.services.
email_templates is exercised directly via asyncio.run for the parts that
need a DB session, and the admin router through TestClient + dependency
overrides for the HTTP surface.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.services import claude_agent, email_templates, rejection_templates
from app.services.auth import get_current_user

client = TestClient(app)

OWNER_EMAIL = settings.owner_email
COLLEAGUE_EMAIL = "colleague@raed.vc"


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return _FakeScalars(self._value)

    def first(self):
        return self._value


class _FakeSession:
    """Pops one queued result per execute() call, in order -- mirrors the
    _RecordingSession pattern in test_rejection_templates.py."""

    def __init__(self, results):
        self._results = list(results)
        self.added: list = []
        self.commits = 0

    async def execute(self, _query):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        pass


class _ExplodingSession:
    """A session that fails the test if the admin gate didn't short-circuit
    before any DB access."""

    async def execute(self, _query):
        raise AssertionError("must not touch the DB before the is_owner check")


def _fake_row(language="en", template_key="MARKET_SIZE", **overrides):
    base = dict(
        id=uuid.uuid4(),
        language=language,
        template_key=template_key,
        subject=rejection_templates._EN_SUBJECT if language == "en" else rejection_templates._AR_SUBJECT,
        body=rejection_templates._EN_BODY_TEMPLATE if language == "en" else rejection_templates._AR_BODY_TEMPLATE,
        reason_text=(rejection_templates._EN_REASONS if language == "en" else rejection_templates._AR_REASONS)[
            template_key
        ],
        updated_by=None,
        updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _auth_as(email: str):
    async def _fake_user():
        return SimpleNamespace(email=email, is_active=True)

    app.dependency_overrides[get_current_user] = _fake_user


def _clear_auth():
    app.dependency_overrides.pop(get_current_user, None)


def _use_db(session):
    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return session


def _clear_db():
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 1. seed_rows() matches the #232 constants exactly, incl. Arabic diacritics
# ---------------------------------------------------------------------------

def test_seed_rows_covers_every_language_and_key():
    rows = email_templates.seed_rows()
    pairs = {(r["language"], r["template_key"]) for r in rows}
    expected = {(lang, key) for lang in ("en", "ar") for key in rejection_templates.TEMPLATE_KEYS}
    assert pairs == expected


def test_seed_rows_english_matches_constants_byte_for_byte():
    rows = {r["template_key"]: r for r in email_templates.seed_rows() if r["language"] == "en"}
    for key in rejection_templates.TEMPLATE_KEYS:
        assert rows[key]["subject"] == rejection_templates._EN_SUBJECT
        assert rows[key]["body"] == rejection_templates._EN_BODY_TEMPLATE
        assert rows[key]["reason_text"] == rejection_templates._EN_REASONS[key]


def test_seed_rows_arabic_matches_constants_including_diacritics():
    rows = {r["template_key"]: r for r in email_templates.seed_rows() if r["language"] == "ar"}
    for key in rejection_templates.TEMPLATE_KEYS:
        assert rows[key]["subject"] == rejection_templates._AR_SUBJECT
        assert rows[key]["body"] == rejection_templates._AR_BODY_TEMPLATE
        assert rows[key]["reason_text"] == rejection_templates._AR_REASONS[key]
    # Spot-check the diacritics called out in issue #234 survive untouched.
    assert "حكمًا" in rejection_templates._AR_BODY_TEMPLATE
    assert any("تمكّن" in v for v in rejection_templates._AR_REASONS.values())
    market_size_reason = rows["MARKET_SIZE"]["reason_text"]
    assert "تمكّن" in market_size_reason


# ---------------------------------------------------------------------------
# 2. Rendering reads the current DB row -- for a lead assessed months ago
#    and one assessed today alike -- with a fallback to the constants when
#    no row exists. No rendered body is ever persisted by this module.
# ---------------------------------------------------------------------------

def test_render_uses_db_row_when_present_not_the_constants():
    row = _fake_row(template_key="MARKET_SIZE", body="{greeting_line}\nDB BODY {company} {reason} {partner_name}")
    session = _FakeSession([row])

    result = asyncio.run(
        email_templates.render_rejection_email(
            session,
            first_name="Sara",
            company="Acme",
            partner_name="Reem",
            reasons=["Market size"],
            language="en",
        )
    )
    assert "DB BODY" in result["body"]
    assert result["body"] != rejection_templates.render_rejection_email(
        first_name="Sara", company="Acme", partner_name="Reem", reasons=["Market size"], language="en",
    )["body"]


def test_render_falls_back_to_constants_when_row_missing():
    session = _FakeSession([None])

    result = asyncio.run(
        email_templates.render_rejection_email(
            session,
            first_name="Sara",
            company="Acme",
            partner_name="Reem",
            reasons=["Market size"],
            language="en",
        )
    )
    expected = rejection_templates.render_rejection_email(
        first_name="Sara", company="Acme", partner_name="Reem", reasons=["Market size"], language="en",
    )
    assert result == expected


def test_old_lead_and_new_lead_both_render_from_current_template_row():
    """No rendered body is ever stamped onto a card -- an "old" lead and a
    "new" one rendered against the same current row get identical output;
    there is no backfill mechanism and none is needed."""
    row = _fake_row(template_key="TRACTION")
    session = _FakeSession([row, row])

    old_lead_result = asyncio.run(
        email_templates.render_rejection_email(
            session, first_name="Old", company="OldCo", partner_name="Reem",
            reasons=["Lack of traction"], language="en",
        )
    )
    new_lead_result = asyncio.run(
        email_templates.render_rejection_email(
            session, first_name="New", company="NewCo", partner_name="Reem",
            reasons=["Lack of traction"], language="en",
        )
    )
    assert rejection_templates._EN_REASONS["TRACTION"] in old_lead_result["body"]
    assert rejection_templates._EN_REASONS["TRACTION"] in new_lead_result["body"]


def test_updating_a_row_changes_the_next_render_for_old_and_new_leads():
    row = _fake_row(template_key="TRACTION")
    session = _FakeSession([row])

    updated = asyncio.run(
        email_templates.update_template(
            session,
            language="en",
            template_key="TRACTION",
            subject="New subject",
            body="{greeting_line}\nUpdated body for {company}. {reason} -- {partner_name}",
            reason_text="a brand-new reason clause.",
            updated_by=OWNER_EMAIL,
        )
    )
    assert updated.subject == "New subject"

    session_old = _FakeSession([row])
    session_new = _FakeSession([row])
    old_lead_result = asyncio.run(
        email_templates.render_rejection_email(
            session_old, first_name="Old", company="OldCo", partner_name="Reem",
            reasons=["Lack of traction"], language="en",
        )
    )
    new_lead_result = asyncio.run(
        email_templates.render_rejection_email(
            session_new, first_name="New", company="NewCo", partner_name="Reem",
            reasons=["Lack of traction"], language="en",
        )
    )
    assert "a brand-new reason clause." in old_lead_result["body"]
    assert "a brand-new reason clause." in new_lead_result["body"]


def test_render_makes_no_llm_call_when_served_from_db(monkeypatch):
    def _fail_if_called(**_kwargs):
        raise AssertionError("_chat_completion must never be entered on the template render path")

    monkeypatch.setattr(claude_agent, "_chat_completion", _fail_if_called)
    row = _fake_row(template_key="CONFLICT")
    session = _FakeSession([row])

    result = asyncio.run(
        email_templates.render_rejection_email(
            session, first_name="Sara", company="Acme", partner_name="Reem",
            reasons=["Conflict of interest"], language="en",
        )
    )
    assert result["template"] == "CONFLICT"


def test_render_invalid_language_raises():
    session = _FakeSession([])
    with pytest.raises(ValueError):
        asyncio.run(
            email_templates.render_rejection_email(
                session, first_name="Sara", company="Acme", partner_name="Reem", reasons=[], language="fr",
            )
        )


# ---------------------------------------------------------------------------
# 3. Validation: empty body, missing required placeholder, unknown placeholder
# ---------------------------------------------------------------------------

def test_validate_body_rejects_empty_body():
    with pytest.raises(email_templates.TemplateValidationError, match="empty"):
        email_templates.validate_body("")


def test_validate_body_rejects_missing_required_placeholder_and_names_it():
    body = "{greeting_line} thanks for sharing. {reason} -- {partner_name}"  # {company} removed
    with pytest.raises(email_templates.TemplateValidationError, match=r"\{company\}"):
        email_templates.validate_body(body)


def test_validate_body_rejects_unknown_placeholder_and_names_it():
    body = "{greeting_line} {company} {reason} {partner_name} {unknown}"
    with pytest.raises(email_templates.TemplateValidationError, match=r"\{unknown\}"):
        email_templates.validate_body(body)


def test_validate_body_rejects_malformed_brace_even_with_all_placeholders_present():
    # All four required placeholders are present, but the stray "{growth"
    # has no closing brace -- str.format() would raise at render time even
    # though the placeholder-set checks above pass it.
    body = "{greeting_line} {company} grew 100% {growth -- {reason} -- {partner_name}"
    with pytest.raises(email_templates.TemplateValidationError, match="malformed"):
        email_templates.validate_body(body)


def test_update_template_rejects_malformed_brace_without_touching_db():
    session = _FakeSession([])  # update_template must validate before any execute()
    body = "{greeting_line} {company} {reason} {partner_name} 100% {growth"
    with pytest.raises(email_templates.TemplateValidationError, match="malformed"):
        asyncio.run(
            email_templates.update_template(
                session, language="en", template_key="MANDATE", subject="Subject",
                body=body, reason_text="reason", updated_by=OWNER_EMAIL,
            )
        )
    assert session.commits == 0


def test_update_template_rejects_bad_body_without_touching_db():
    session = _FakeSession([])  # update_template must validate before any execute()
    with pytest.raises(email_templates.TemplateValidationError):
        asyncio.run(
            email_templates.update_template(
                session, language="en", template_key="MANDATE", subject="Subject",
                body="{greeting_line} {company} {reason}",  # missing {partner_name}
                reason_text="reason", updated_by=OWNER_EMAIL,
            )
        )
    assert session.commits == 0


# ---------------------------------------------------------------------------
# 4. Revert restores subject, body, and reason_text together as one version
# ---------------------------------------------------------------------------

def test_revert_restores_subject_body_and_reason_text_as_one_unit():
    row = _fake_row(template_key="MANDATE", subject="Current subject", body="current {greeting_line} {company} {reason} {partner_name}", reason_text="current reason")
    version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=version_id,
        template_id=row.id,
        subject="Old subject",
        body="old {greeting_line} {company} {reason} {partner_name}",
        reason_text="old reason",
        saved_by=None,
        saved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session = _FakeSession([row, version])

    reverted = asyncio.run(
        email_templates.revert_template(
            session, language="en", template_key="MANDATE", version_id=version_id, updated_by=OWNER_EMAIL,
        )
    )
    assert reverted.subject == "Old subject"
    assert reverted.body == "old {greeting_line} {company} {reason} {partner_name}"
    assert reverted.reason_text == "old reason"
    # The pre-revert state was snapshotted before being overwritten.
    assert len(session.added) == 1
    snapshot = session.added[0]
    assert snapshot.subject == "Current subject"
    assert snapshot.reason_text == "current reason"


def test_revert_unknown_version_raises_lookup_error():
    row = _fake_row(template_key="MANDATE")
    session = _FakeSession([row, None])
    with pytest.raises(LookupError):
        asyncio.run(
            email_templates.revert_template(
                session, language="en", template_key="MANDATE", version_id=uuid.uuid4(), updated_by=OWNER_EMAIL,
            )
        )


# ---------------------------------------------------------------------------
# 5. Admin-gated write endpoints -- 403 for non-admins, on every write route
# ---------------------------------------------------------------------------

def test_non_admin_gets_403_listing_templates():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db(_ExplodingSession())
    try:
        response = client.get("/api/v1/email-templates")
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 403


def test_non_admin_gets_403_updating_a_template():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db(_ExplodingSession())
    try:
        response = client.put(
            "/api/v1/email-templates/en/MANDATE",
            json={"subject": "s", "body": "{greeting_line} {company} {reason} {partner_name}", "reason_text": "r"},
        )
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 403


def test_non_admin_gets_403_listing_versions():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db(_ExplodingSession())
    try:
        response = client.get("/api/v1/email-templates/en/MANDATE/versions")
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 403


def test_non_admin_gets_403_reverting_a_template():
    _auth_as(COLLEAGUE_EMAIL)
    _use_db(_ExplodingSession())
    try:
        response = client.post(f"/api/v1/email-templates/en/MANDATE/revert/{uuid.uuid4()}")
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 6. Admin happy paths + validation surfacing through the HTTP layer
# ---------------------------------------------------------------------------

def test_admin_can_list_all_templates():
    rows = [_fake_row(language=lang, template_key=key) for lang in ("en", "ar") for key in rejection_templates.TEMPLATE_KEYS]
    _auth_as(OWNER_EMAIL)
    _use_db(_FakeSession([rows]))
    try:
        response = client.get("/api/v1/email-templates")
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 200
    assert len(response.json()) == 8


def test_admin_update_with_removed_placeholder_is_400_naming_it():
    _auth_as(OWNER_EMAIL)
    _use_db(_FakeSession([]))
    try:
        response = client.put(
            "/api/v1/email-templates/en/MANDATE",
            json={
                "subject": "Subject",
                "body": "{greeting_line} thanks. {reason} -- {partner_name}",  # {company} removed
                "reason_text": "reason",
            },
        )
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 400
    assert "company" in response.json()["detail"]


def test_admin_update_with_unknown_placeholder_is_400_naming_it():
    _auth_as(OWNER_EMAIL)
    _use_db(_FakeSession([]))
    try:
        response = client.put(
            "/api/v1/email-templates/en/MANDATE",
            json={
                "subject": "Subject",
                "body": "{greeting_line} {company} {reason} {partner_name} {unknown}",
                "reason_text": "reason",
            },
        )
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 400
    assert "unknown" in response.json()["detail"]


def test_admin_update_with_empty_body_is_400():
    _auth_as(OWNER_EMAIL)
    _use_db(_FakeSession([]))
    try:
        response = client.put(
            "/api/v1/email-templates/en/MANDATE",
            json={"subject": "Subject", "body": "   ", "reason_text": "reason"},
        )
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_admin_update_success_returns_updated_row():
    row = _fake_row(template_key="MANDATE")
    _auth_as(OWNER_EMAIL)
    _use_db(_FakeSession([row]))
    try:
        response = client.put(
            "/api/v1/email-templates/en/MANDATE",
            json={
                "subject": "New subject",
                "body": "{greeting_line} {company} {reason} {partner_name}",
                "reason_text": "new reason",
            },
        )
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "New subject"
    assert body["reason_text"] == "new reason"
    assert body["updated_by"] == OWNER_EMAIL


def test_admin_revert_success_restores_all_three_fields():
    row = _fake_row(template_key="MANDATE", subject="Current", body="current {greeting_line} {company} {reason} {partner_name}", reason_text="current reason")
    version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=version_id, template_id=row.id, subject="Old", body="old {greeting_line} {company} {reason} {partner_name}",
        reason_text="old reason", saved_by=None, saved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _auth_as(OWNER_EMAIL)
    _use_db(_FakeSession([row, version]))
    try:
        response = client.post(f"/api/v1/email-templates/en/MANDATE/revert/{version_id}")
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Old"
    assert body["body"] == "old {greeting_line} {company} {reason} {partner_name}"
    assert body["reason_text"] == "old reason"


def test_invalid_language_in_path_is_400():
    _auth_as(OWNER_EMAIL)
    _use_db(_ExplodingSession())
    try:
        response = client.get("/api/v1/email-templates/fr/MANDATE/versions")
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 400


def test_invalid_template_key_in_path_is_400():
    _auth_as(OWNER_EMAIL)
    _use_db(_ExplodingSession())
    try:
        response = client.get("/api/v1/email-templates/en/NOT_A_KEY/versions")
    finally:
        _clear_auth()
        _clear_db()
    assert response.status_code == 400
