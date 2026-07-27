"""
Tests for app.services.email_sender: From/Reply-To headers can be overridden
per-send (issue #85, "send outreach from the lead owner's email") while
authentication and the envelope sender stay the shared SMTP account. No real
network call is made -- smtplib.SMTP is replaced with a fake that just
records the constructed EmailMessage.
"""
import smtplib

import pytest

from app.config import settings
from app.services import email_sender


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=20):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, msg, from_addr=None):
        _FakeSMTP.sent.append(msg)
        _FakeSMTP.from_addrs.append(from_addr)


@pytest.fixture(autouse=True)
def _configure_smtp(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "mail_from", "deals@raed.vc")
    monkeypatch.setattr(settings, "mail_from_name", "Raed Ventures")
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []
    _FakeSMTP.from_addrs = []
    yield


def test_send_email_with_owner_override_sets_from_and_reply_to():
    email_sender.send_email(
        "founder@acme.test",
        "Let's talk",
        "Hi there",
        from_addr="waleed@raed.vc",
        from_name="Waleed",
        reply_to="waleed@raed.vc",
    )

    msg = _FakeSMTP.sent[0]
    assert msg["From"] == "Waleed <waleed@raed.vc>"
    assert msg["Reply-To"] == "waleed@raed.vc"
    assert msg["To"] == "founder@acme.test"
    assert _FakeSMTP.from_addrs[0] == "deals@raed.vc"


def test_send_email_without_override_defaults_to_mail_from():
    """Back-compat: existing callers that don't pass from_addr/from_name/
    reply_to keep sending as the global mail_from, with no Reply-To header."""
    email_sender.send_email("founder@acme.test", "Let's talk", "Hi there")

    msg = _FakeSMTP.sent[0]
    assert msg["From"] == "Raed Ventures <deals@raed.vc>"
    assert msg["Reply-To"] is None
    assert _FakeSMTP.from_addrs[0] == "deals@raed.vc"
