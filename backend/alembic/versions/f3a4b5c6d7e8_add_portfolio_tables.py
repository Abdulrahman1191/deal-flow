"""add portfolio intelligence tables

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sector", sa.String(64)),
        sa.Column("region", sa.String(64)),
        sa.Column("founder_names", postgresql.ARRAY(sa.String(255))),
        sa.Column("website", sa.String(255)),
        sa.Column("description", sa.Text),
        sa.Column("our_decision", sa.String(16), nullable=False),  # FUNDED | PASSED | NOT_SEEN
        sa.Column("decision_at", sa.Date),
        sa.Column("decision_rationale", sa.Text),
        sa.Column("invested_amount_usd", sa.BigInteger),
        # Denormalized latest outcome state for fast list-view queries.
        # Updated whenever a new portfolio_outcomes row is added.
        sa.Column("current_status", sa.String(16), nullable=False, server_default="too_early"),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_portfolio_companies_decision", "portfolio_companies", ["our_decision"])
    op.create_index("ix_portfolio_companies_status", "portfolio_companies", ["current_status"])

    op.create_table(
        "portfolio_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("portfolio_companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("current_valuation_usd", sa.BigInteger),
        sa.Column("last_round_stage", sa.String(32)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_portfolio_outcomes_company", "portfolio_outcomes", ["company_id", "recorded_at"])

    op.create_table(
        "portfolio_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("portfolio_companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_type", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),  # POSITIVE | NEGATIVE
        sa.Column("weight", sa.SmallInteger, nullable=False),
        sa.Column("observed_at", sa.Date),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("weight BETWEEN 1 AND 5", name="ck_signal_weight_range"),
    )
    op.create_index("ix_portfolio_signals_company", "portfolio_signals", ["company_id"])
    op.create_index("ix_portfolio_signals_type", "portfolio_signals", ["signal_type"])


def downgrade() -> None:
    op.drop_index("ix_portfolio_signals_type", table_name="portfolio_signals")
    op.drop_index("ix_portfolio_signals_company", table_name="portfolio_signals")
    op.drop_table("portfolio_signals")
    op.drop_index("ix_portfolio_outcomes_company", table_name="portfolio_outcomes")
    op.drop_table("portfolio_outcomes")
    op.drop_index("ix_portfolio_companies_status", table_name="portfolio_companies")
    op.drop_index("ix_portfolio_companies_decision", table_name="portfolio_companies")
    op.drop_table("portfolio_companies")
