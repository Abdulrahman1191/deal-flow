from __future__ import annotations
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Controlled vocabularies — kept here in code (not as DB enums) so we can add
# new values without a migration. See PORTFOLIO_INTELLIGENCE_PLAN.md for
# the canonical list and meanings.

DECISION_VALUES = {"FUNDED", "PASSED", "NOT_SEEN"}

OUTCOME_STATES = {
    "exited", "growing", "stalled", "zombie", "failed", "acqui_hire", "too_early",
}

SIGNAL_TYPES = {
    "founder_execution",
    "founder_domain_fit",
    "founder_team_chemistry",
    "market_timing",
    "market_size",
    "tech_moat_durability",
    "distribution_capability",
    "capital_efficiency",
    "regulatory_environment",
    "unit_economics",
    "pivot_required",
    "macro_tailwind",
    "competitive_pressure",
    "customer_concentration",
    "data_advantage",
}

DIRECTION_VALUES = {"POSITIVE", "NEGATIVE"}


class PortfolioCompany(Base):
    __tablename__ = "portfolio_companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector: Mapped[Optional[str]] = mapped_column(String(64))
    region: Mapped[Optional[str]] = mapped_column(String(64))
    founder_names: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String(255)))
    website: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    our_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_at: Mapped[Optional[date]] = mapped_column(Date)
    decision_rationale: Mapped[Optional[str]] = mapped_column(Text)
    invested_amount_usd: Mapped[Optional[int]] = mapped_column(BigInteger)
    current_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="too_early")
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    outcomes: Mapped[list["PortfolioOutcome"]] = relationship(
        "PortfolioOutcome", back_populates="company", cascade="all, delete-orphan",
        order_by="PortfolioOutcome.recorded_at.desc()",
    )
    signals: Mapped[list["PortfolioSignal"]] = relationship(
        "PortfolioSignal", back_populates="company", cascade="all, delete-orphan",
        order_by="PortfolioSignal.created_at.desc()",
    )


class PortfolioOutcome(Base):
    __tablename__ = "portfolio_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolio_companies.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    current_valuation_usd: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_round_stage: Mapped[Optional[str]] = mapped_column(String(32))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped["PortfolioCompany"] = relationship("PortfolioCompany", back_populates="outcomes")


class PortfolioSignal(Base):
    __tablename__ = "portfolio_signals"
    __table_args__ = (CheckConstraint("weight BETWEEN 1 AND 5", name="ck_signal_weight_range"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolio_companies.id", ondelete="CASCADE"), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    weight: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    observed_at: Mapped[Optional[date]] = mapped_column(Date)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped["PortfolioCompany"] = relationship("PortfolioCompany", back_populates="signals")
