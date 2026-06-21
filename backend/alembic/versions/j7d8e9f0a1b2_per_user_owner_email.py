"""per-user ownership: owner_email on leads/portfolio/briefings, users.copper_user_id

Adds per-user data isolation. Existing rows are backfilled to abdulrahman@raed.vc
so the current pipeline (Abdulrahman's Copper sync) keeps working unchanged.

Revision ID: j7d8e9f0a1b2
Revises: i6c7d8e9f0a1
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "j7d8e9f0a1b2"
down_revision: Union[str, None] = "i6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_OWNER = "abdulrahman@raed.vc"


def upgrade() -> None:
    # --- leads ---
    op.add_column("leads", sa.Column("owner_email", sa.String(length=255), nullable=True))
    op.execute(f"UPDATE leads SET owner_email = '{_DEFAULT_OWNER}' WHERE owner_email IS NULL")
    op.create_index("ix_leads_owner_email", "leads", ["owner_email"])

    # --- portfolio_companies ---
    op.add_column("portfolio_companies", sa.Column("owner_email", sa.String(length=255), nullable=True))
    op.execute(f"UPDATE portfolio_companies SET owner_email = '{_DEFAULT_OWNER}' WHERE owner_email IS NULL")
    op.create_index("ix_portfolio_companies_owner_email", "portfolio_companies", ["owner_email"])

    # --- daily_briefings: one per (date, owner) instead of one per date ---
    op.add_column("daily_briefings", sa.Column("owner_email", sa.String(length=255), nullable=True))
    op.execute(f"UPDATE daily_briefings SET owner_email = '{_DEFAULT_OWNER}' WHERE owner_email IS NULL")
    op.create_index("ix_daily_briefings_owner_email", "daily_briefings", ["owner_email"])
    # Drop the old single-column unique (Postgres default name) if present, add composite.
    op.execute("ALTER TABLE daily_briefings DROP CONSTRAINT IF EXISTS daily_briefings_date_key")
    op.create_unique_constraint("uq_briefing_date_owner", "daily_briefings", ["date", "owner_email"])

    # --- users: cache resolved Copper user id ---
    op.add_column("users", sa.Column("copper_user_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "copper_user_id")

    op.drop_constraint("uq_briefing_date_owner", "daily_briefings", type_="unique")
    op.create_unique_constraint("daily_briefings_date_key", "daily_briefings", ["date"])
    op.drop_index("ix_daily_briefings_owner_email", table_name="daily_briefings")
    op.drop_column("daily_briefings", "owner_email")

    op.drop_index("ix_portfolio_companies_owner_email", table_name="portfolio_companies")
    op.drop_column("portfolio_companies", "owner_email")

    op.drop_index("ix_leads_owner_email", table_name="leads")
    op.drop_column("leads", "owner_email")
