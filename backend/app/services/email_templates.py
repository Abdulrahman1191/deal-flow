"""
Database-backed rejection email templates (issue #234).

The four templates from app.services.rejection_templates (issue #232) move
out of Python constants into the `email_templates` table, keyed by
(language, template_key), so an admin can edit copy without a deploy -- and
so the edit reaches every unsent lead, assessed yesterday or months ago,
with no backfill.

render_rejection_email() is the choke point the send path
(app.routers.assessments) renders through: it reads the current row for the
selected template and falls back to the #232 constants, byte for byte, when
no row exists (fresh DB, or a key that was never seeded). Only the template
itself is persisted -- never a rendered body -- so a copy edit applies
retroactively to every lead that hasn't been sent yet.

email_template_versions holds one row per prior save (update_template) and
one per revert (revert_template snapshots the state it's about to
overwrite), so any earlier subject/body/reason_text combination can be
restored as a single unit.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_template import EmailTemplate, EmailTemplateVersion
from app.services import rejection_templates

LANGUAGES: tuple[str, ...] = ("en", "ar")

# {first_name} is deliberately absent -- the #232 constants already resolved
# the "no name" fallback into {greeting_line} (rejection_templates.py:129)
# because Arabic can't do that fallback via simple substitution.
REQUIRED_PLACEHOLDERS: frozenset[str] = frozenset({"greeting_line", "company", "reason", "partner_name"})
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class TemplateValidationError(ValueError):
    """Raised with a message naming the offending field/placeholder."""


def seed_rows() -> list[dict]:
    """One dict per (language, template_key), read byte for byte from the
    #232 constants. The seed migration and this module's tests both read
    from here, so the DB rows and the Python constants can never drift
    apart on day one -- including the Arabic diacritics."""
    rows: list[dict] = []
    for key in rejection_templates.TEMPLATE_KEYS:
        rows.append(
            {
                "language": "en",
                "template_key": key,
                "subject": rejection_templates._EN_SUBJECT,
                "body": rejection_templates._EN_BODY_TEMPLATE,
                "reason_text": rejection_templates._EN_REASONS[key],
            }
        )
        rows.append(
            {
                "language": "ar",
                "template_key": key,
                "subject": rejection_templates._AR_SUBJECT,
                "body": rejection_templates._AR_BODY_TEMPLATE,
                "reason_text": rejection_templates._AR_REASONS[key],
            }
        )
    return rows


def validate_body(body: str) -> None:
    """Rejects an empty body, a body missing a required placeholder, or one
    carrying a placeholder outside the known set -- naming the offending
    placeholder rather than failing generically. Also rejects a body that
    would raise at render time (e.g. a stray/unmatched brace) even when the
    four required placeholders are all present elsewhere, by attempting the
    same str.format() call render_rejection_email() does."""
    if not body or not body.strip():
        raise TemplateValidationError("body must not be empty")
    found = set(_PLACEHOLDER_RE.findall(body))
    missing = REQUIRED_PLACEHOLDERS - found
    if missing:
        raise TemplateValidationError(f"body is missing required placeholder {{{sorted(missing)[0]}}}")
    unknown = found - REQUIRED_PLACEHOLDERS
    if unknown:
        raise TemplateValidationError(f"body contains unknown placeholder {{{sorted(unknown)[0]}}}")
    try:
        body.format(**{p: "" for p in REQUIRED_PLACEHOLDERS})
    except (ValueError, KeyError, IndexError) as exc:
        raise TemplateValidationError(f"body has a malformed placeholder or brace: {exc}") from exc


async def get_template(db: AsyncSession, language: str, template_key: str) -> Optional[EmailTemplate]:
    result = await db.execute(
        select(EmailTemplate).where(
            EmailTemplate.language == language,
            EmailTemplate.template_key == template_key,
        )
    )
    return result.scalar_one_or_none()


async def list_templates(db: AsyncSession) -> list[EmailTemplate]:
    result = await db.execute(select(EmailTemplate).order_by(EmailTemplate.language, EmailTemplate.template_key))
    return list(result.scalars().all())


async def list_versions(db: AsyncSession, *, language: str, template_key: str) -> list[EmailTemplateVersion]:
    row = await get_template(db, language, template_key)
    if row is None:
        return []
    result = await db.execute(
        select(EmailTemplateVersion)
        .where(EmailTemplateVersion.template_id == row.id)
        .order_by(EmailTemplateVersion.saved_at.desc())
    )
    return list(result.scalars().all())


def _snapshot_version(row: EmailTemplate) -> EmailTemplateVersion:
    return EmailTemplateVersion(
        id=uuid.uuid4(),
        template_id=row.id,
        language=row.language,
        template_key=row.template_key,
        subject=row.subject,
        body=row.body,
        reason_text=row.reason_text,
        saved_by=row.updated_by,
        saved_at=row.updated_at,
    )


async def update_template(
    db: AsyncSession,
    *,
    language: str,
    template_key: str,
    subject: str,
    body: str,
    reason_text: str,
    updated_by: str,
) -> EmailTemplate:
    """Validates, snapshots the current row into email_template_versions,
    then overwrites it -- so the version being replaced is never lost."""
    if not subject or not subject.strip():
        raise TemplateValidationError("subject must not be empty")
    if not reason_text or not reason_text.strip():
        raise TemplateValidationError("reason_text must not be empty")
    validate_body(body)

    row = await get_template(db, language, template_key)
    if row is None:
        raise LookupError(f"No email_templates row for language={language!r} template_key={template_key!r}")

    db.add(_snapshot_version(row))

    row.subject = subject
    row.body = body
    row.reason_text = reason_text
    row.updated_by = updated_by
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def revert_template(
    db: AsyncSession,
    *,
    language: str,
    template_key: str,
    version_id: uuid.UUID,
    updated_by: str,
) -> EmailTemplate:
    """Restores subject, body, and reason_text together as one unit from a
    prior version row -- after snapshotting the current state, so the
    revert itself is undoable too."""
    row = await get_template(db, language, template_key)
    if row is None:
        raise LookupError(f"No email_templates row for language={language!r} template_key={template_key!r}")

    result = await db.execute(
        select(EmailTemplateVersion).where(
            EmailTemplateVersion.id == version_id,
            EmailTemplateVersion.template_id == row.id,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise LookupError(f"No version {version_id} for language={language!r} template_key={template_key!r}")

    db.add(_snapshot_version(row))

    row.subject = version.subject
    row.body = version.body
    row.reason_text = version.reason_text
    row.updated_by = updated_by
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def render_rejection_email(
    db: AsyncSession,
    *,
    first_name: Optional[str],
    company: Optional[str],
    partner_name: Optional[str],
    reasons: Optional[list[str]],
    language: str,
) -> dict[str, str]:
    """Renders a rejection email from the current email_templates row for
    the selected template, falling back to the #232 Python constants when
    no row exists. Never stores the rendered body anywhere but the caller's
    response -- the same lead rendered again tomorrow, after an admin edits
    the row, gets tomorrow's copy with no backfill and no re-assessment."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be 'en' or 'ar', got {language!r}")

    template_key = rejection_templates.select_template(reasons)
    row = await get_template(db, language, template_key)
    if row is None:
        return rejection_templates.render_rejection_email(
            first_name=first_name,
            company=company,
            partner_name=partner_name,
            reasons=reasons,
            language=language,
        )

    greeting_line = rejection_templates._greeting_line(first_name, language)
    body = row.body.format(
        greeting_line=greeting_line,
        company=(company or "").strip(),
        reason=row.reason_text,
        partner_name=(partner_name or "").strip(),
    )
    return {"subject": row.subject, "body": body, "template": template_key, "language": language}
