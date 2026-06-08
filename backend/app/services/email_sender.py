"""
Outbound email via SMTP (works with SES or SendGrid — both expose SMTP).

Kept deliberately simple: plain-text send over STARTTLS using the SMTP creds in
settings. mail_from must be a verified sender/domain at the provider. If SMTP
isn't configured, is_configured() is False and callers should refuse to "send"
(so we never silently mark a lead sent without an email going out).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import settings


def is_configured() -> bool:
    return bool(settings.smtp_host and settings.mail_from)


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Raises on any failure (caller must handle)."""
    if not is_configured():
        raise RuntimeError("Email not configured: set SMTP_HOST and MAIL_FROM (SES or SendGrid).")
    if not to:
        raise ValueError("No recipient email address.")

    msg = EmailMessage()
    from_addr = settings.mail_from
    msg["From"] = f"{settings.mail_from_name} <{from_addr}>" if settings.mail_from_name else from_addr
    msg["To"] = to
    msg["Subject"] = subject or ""
    msg.set_content(body or "")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)
