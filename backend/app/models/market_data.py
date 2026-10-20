"""Daily prices and original SEC filing facts."""
from datetime import date as CalendarDate
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (
        CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0 AND adjusted_close > 0", name="ck_prices_positive"),
        CheckConstraint("volume >= 0", name="ck_prices_volume"),
        CheckConstraint("high + 0.000002 >= low AND high + 0.000002 >= open AND high + 0.000002 >= close AND low - 0.000002 <= open AND low - 0.000002 <= close", name="ck_prices_ohlc"),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    date: Mapped[CalendarDate] = mapped_column(primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(32, 18))
    high: Mapped[Decimal] = mapped_column(Numeric(32, 18))
    low: Mapped[Decimal] = mapped_column(Numeric(32, 18))
    close: Mapped[Decimal] = mapped_column(Numeric(32, 18))
    adjusted_close: Mapped[Decimal] = mapped_column(Numeric(32, 18))
    volume: Mapped[int] = mapped_column(BigInteger)


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint("company_id", "taxonomy", "tag", "unit", "period_start", "period_end", "accession", name="uq_financial_fact"),
        CheckConstraint("period_start <= period_end", name="ck_fact_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    taxonomy: Mapped[str] = mapped_column(String(32))
    tag: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(32))
    value: Mapped[Decimal] = mapped_column(Numeric(38, 10))
    period_start: Mapped[CalendarDate] = mapped_column()
    period_end: Mapped[CalendarDate] = mapped_column()
    filed: Mapped[CalendarDate] = mapped_column()
    accession: Mapped[str] = mapped_column(String(32))
    form: Mapped[str] = mapped_column(String(16))
    fiscal_period: Mapped[str | None] = mapped_column(String(16))
    period_kind: Mapped[str] = mapped_column(String(32))
