"""
Tests for TEAM_EMAILS pre-provisioning: creating `users` rows for configured
teammates ahead of their first sign-in, so the periodic Copper sync
(app/tasks/sync_copper.py::_run_all) can import their leads before they ever
log in.

Uses plain fakes rather than a live Postgres — same rationale as
test_multiuser_access.py from issue #21: no DB service is available in CI.
"""
from __future__ import annotations
import asyncio

from app.config import settings
from app.tasks.sync_copper import provision_team_users


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _FakeSession:
    """Records added rows + whether commit() was called, and answers the
    "which of these emails already exist" query with a canned list."""

    def __init__(self, existing_emails):
        self._existing = list(existing_emails)
        self.added: list = []
        self.committed = False

    async def execute(self, _query):
        return _FakeResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


OWNER = settings.owner_email.strip().lower()


# ---------- Settings.team_email_list ----------


def test_team_email_list_lowercases_and_dedupes(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "Waleed@raed.vc, yomna@raed.vc, waleed@raed.vc")
    emails = settings.team_email_list()
    assert emails.count("waleed@raed.vc") == 1
    assert "yomna@raed.vc" in emails


def test_team_email_list_always_includes_owner(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "waleed@raed.vc")
    emails = settings.team_email_list()
    assert OWNER in emails


def test_team_email_list_skips_blank_entries(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "waleed@raed.vc,, ,yomna@raed.vc,")
    emails = settings.team_email_list()
    assert "" not in emails
    assert set(emails) == {"waleed@raed.vc", "yomna@raed.vc", OWNER}


def test_team_email_list_empty_defaults_to_owner_only(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "")
    assert settings.team_email_list() == [OWNER]


# ---------- provision_team_users ----------


def test_provision_creates_missing_rows(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "waleed@raed.vc,yomna@raed.vc")
    session = _FakeSession(existing_emails=[])

    created = asyncio.run(provision_team_users(session))

    assert {u.email for u in created} == {"waleed@raed.vc", "yomna@raed.vc", OWNER}
    for user in created:
        assert user.hashed_password == "platform-managed"
        assert user.is_active is True
    assert session.added == created
    assert session.committed is True


def test_provision_is_idempotent_for_existing_rows(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "waleed@raed.vc,yomna@raed.vc")
    session = _FakeSession(existing_emails=["waleed@raed.vc", "yomna@raed.vc", OWNER])

    created = asyncio.run(provision_team_users(session))

    assert created == []
    assert session.added == []
    assert session.committed is False


def test_provision_only_creates_the_missing_user(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "waleed@raed.vc,yomna@raed.vc")
    session = _FakeSession(existing_emails=["waleed@raed.vc", OWNER])

    created = asyncio.run(provision_team_users(session))

    assert {u.email for u in created} == {"yomna@raed.vc"}
    assert session.committed is True


def test_provision_still_provisions_owner_when_team_emails_unset(monkeypatch):
    monkeypatch.setattr(settings, "team_emails", "")
    session = _FakeSession(existing_emails=[])

    created = asyncio.run(provision_team_users(session))

    assert {u.email for u in created} == {OWNER}
