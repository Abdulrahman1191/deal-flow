"""rejection copy: drop the 'today' line, sign as the team

Issue #249: three copy fixes to the rejection email.

  1. Drops "This reflects where Raed Ventures is today rather than a
     judgement on what you are building." (EN) / the matching Arabic
     sentence -- asked for in #234, no longer wanted.
  2. Signs off as "Raed Ventures team" / "فريق رائد فنتشرز" literal text
     instead of the {partner_name} placeholder -- these emails send from
     submission@raed.vc, not a partner's personal mailbox.

Only rewrites a row whose `body` still matches the byte-for-byte text
app.services.email_templates.seed_rows() produced before this issue --
`body` is the template string (not reason-substituted) and is identical
across all four template_keys within a language, so this only needs to
compare two strings, not one per row. A row an admin already hand-edited
via the Email templates screen (issue #241) no longer matches and is left
alone, exactly as the issue asks.

Reversible: downgrade() restores a row whose body matches the new text back
to the old text -- the mirror image of upgrade(), so a row touched by hand
in between is equally left alone on the way back down.

Revision ID: y3z4a5b6c7d8
Revises: c5d6e7f8a9b0
Create Date: 2026-10-19
"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "y3z4a5b6c7d8"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

email_templates_table = sa.table(
    "email_templates",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("language", sa.String),
    sa.column("body", sa.Text),
)

_OLD_EN_BODY = """{greeting_line}

Thank you for sharing {company} with us, and for the time you put into the application.

After reviewing your application, {reason}

This reflects where Raed Ventures is today rather than a judgement on what you are building.

We wish you the best of luck with it.

{partner_name}
Raed Ventures"""

_NEW_EN_BODY = """{greeting_line}

Thank you for sharing {company} with us, and for the time you put into the application.

After reviewing your application, {reason}

We wish you the best of luck with it.

Raed Ventures team"""

_OLD_AR_BODY = """{greeting_line}

شكراً لمشاركتكم {company} معنا، وعلى الوقت الذي خصصتموه لتقديم الطلب.

لقد اطلعنا على الفرصة. {reason}

هذا القرار يعكس وضع رائد فنتشرز في الوقت الحالي أكثر مما يعكس حكمًا على ما يتم بناؤه.

نتمنى لكم التوفيق.

{partner_name}
رائد فنتشرز"""

_NEW_AR_BODY = """{greeting_line}

شكراً لمشاركتكم {company} معنا، وعلى الوقت الذي خصصتموه لتقديم الطلب.

لقد اطلعنا على الفرصة. {reason}

نتمنى لكم التوفيق.

فريق رائد فنتشرز"""


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        email_templates_table.update()
        .where(email_templates_table.c.language == "en", email_templates_table.c.body == _OLD_EN_BODY)
        .values(body=_NEW_EN_BODY)
    )
    connection.execute(
        email_templates_table.update()
        .where(email_templates_table.c.language == "ar", email_templates_table.c.body == _OLD_AR_BODY)
        .values(body=_NEW_AR_BODY)
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        email_templates_table.update()
        .where(email_templates_table.c.language == "en", email_templates_table.c.body == _NEW_EN_BODY)
        .values(body=_OLD_EN_BODY)
    )
    connection.execute(
        email_templates_table.update()
        .where(email_templates_table.c.language == "ar", email_templates_table.c.body == _NEW_AR_BODY)
        .values(body=_OLD_AR_BODY)
    )
