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


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    from_addr: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
) -> None:
    """Send a plain-text email. Raises on any failure (caller must handle).

    `from_addr`/`from_name` let a caller send as a specific person (e.g. the
    lead owner) while still authenticating and sending over the shared SMTP
    account — only the visible From/Reply-To change, not the envelope sender.
    Default to `settings.mail_from`/`mail_from_name` for back-compat with
    existing callers. `reply_to` is only set on the message when given.
    """
    if not is_configured():
        raise RuntimeError("Email not configured: set SMTP_HOST and MAIL_FROM (SES or SendGrid).")
    if not to:
        raise ValueError("No recipient email address.")

    resolved_from_addr = from_addr or settings.mail_from
    resolved_from_name = from_name if from_name is not None else settings.mail_from_name

    msg = EmailMessage()
    msg["From"] = f"{resolved_from_name} <{resolved_from_addr}>" if resolved_from_name else resolved_from_addr
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject or ""
    msg.set_content(body or "")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg, from_addr=settings.mail_from)
