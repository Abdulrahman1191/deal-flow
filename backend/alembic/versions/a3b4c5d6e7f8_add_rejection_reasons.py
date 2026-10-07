"""add rejection_reasons columns

Lets the partner pick rejection reasons when regenerating a REJECT draft
(issue #223), instead of every rejection email reading the same.

  - assessment_cards.rejection_reasons: canonical UNQUAL_REASON_OPTIONS labels
    the partner selected, persisted so a later archive/send can drive Copper's
    Unqualification Reasons field (CF 244358) from the human choice instead of
    claude_agent.generate_unqualification_reason -- see
    claude_agent.resolve_unqualification_reason. Null until a reasoned
    regeneration happens.
  - assessment_overrides.human_rejection_reasons: snapshot of the above at
    training-row capture time (app.services.override_capture), distinct from
    the existing free-text human_reason/human_reason_tags columns.

Both nullable -- existing rows, and any draft regenerated before this shipped,
have no selection to record.

Revision ID: a3b4c5d6e7f8
Revises: c5e89de040a4
Create Date: 2026-10-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "c5e89de040a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_cards",
        sa.Column("rejection_reasons", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "assessment_overrides",
        sa.Column("human_rejection_reasons", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_overrides", "human_rejection_reasons")
    op.drop_column("assessment_cards", "rejection_reasons")
