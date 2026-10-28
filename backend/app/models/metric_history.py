"""Point-in-time versions of annual metric cells, rebuilt from facts and rules."""

from datetime import date as CalendarDate
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnnualMetricVersion(Base):
    """One cell version: valid from the filing date until the next version of the cell."""

    __tablename__ = "annual_metric_versions"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period_start",
            "period_end",
            "metric",
            "known_from",
            name="uq_metric_version",
        ),
        CheckConstraint("period_start <= period_end", name="ck_version_period"),
        CheckConstraint(
            "metric IN ('revenue', 'net_income', 'eps_diluted', 'equity', "
            "'operating_cash_flow')",
            name="ck_version_metric",
        ),
        CheckConstraint(
            "status IN ('ready', 'needs_review', 'not_available')",
            name="ck_version_status",
        ),
        CheckConstraint(
            "confirmation IN ('approved', 'identical', 'rounding', 'split', "
            "'reviewed')",
            name="ck_version_confirmation",
        ),
        # A value is served only when it is confirmed, and every ready cell has one.
        CheckConstraint(
            "(status = 'ready') = (value IS NOT NULL AND confirmation IS NOT NULL)",
            name="ck_version_ready",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    period_start: Mapped[CalendarDate] = mapped_column()
    period_end: Mapped[CalendarDate] = mapped_column()
    metric: Mapped[str] = mapped_column(String(32))
    # SEC filing date of the report that made this version known (inclusive).
    known_from: Mapped[CalendarDate] = mapped_column()
    accession: Mapped[str] = mapped_column(String(32))
    form: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    confirmation: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[Decimal | None] = mapped_column(Numeric(38, 10))
    # Every AnnualMetric field except variants, exactly as the API serves it (including
    # the value's text); variants are attached from facts at request time.
    details: Mapped[dict] = mapped_column(JSONB)
