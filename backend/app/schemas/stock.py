from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer


def decimal_string(value: Decimal) -> str:
    """Write one canonical form of a number in HTTP responses.

    Every significant digit is kept and scientific notation is avoided. Trailing
    zeros only reflect the storage scale (database columns pad to ten places,
    reviewed values do not), so they are removed: 9.7700000000 and 9.77 are
    the same number and must look the same to API clients.
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    return text


DecimalValue = Annotated[Decimal, PlainSerializer(decimal_string, return_type=str)]


class Stock(BaseModel):
    ticker: str
    name: str
    exchange: str
    sector: str | None = None
    financial_history_note: str | None = None


class Price(BaseModel):
    date: date
    open: DecimalValue
    high: DecimalValue
    low: DecimalValue
    close: DecimalValue
    adjusted_close: DecimalValue
    volume: int


class FilingValue(BaseModel):
    source_cik: str | None = None
    taxonomy: str
    tag: str
    unit: str
    value: DecimalValue
    period_start: date
    period_end: date
    filed: date
    accession: str
    form: str
    statement_url: str | None = None
    statement_label: str | None = None
    statement_column: str | None = None


class EpsSplit(BaseModel):
    date: date
    new_shares: int = Field(gt=0, strict=True)
    old_shares: int = Field(gt=0, strict=True)
    source_url: str


class EpsShareBasis(BaseModel):
    as_of: date
    reported_value: DecimalValue
    new_shares: int = Field(gt=0)
    old_shares: int = Field(gt=0)
    applied_splits: list[EpsSplit]


class GrowthReport(BaseModel):
    """The annual report growth was read from, with both years as it states them."""

    accession: str
    filed: date
    source_cik: str
    earlier_period_end: date
    earlier_value: DecimalValue = Field(
        description="The earlier year as this report states it, restated if it was."
    )
    later_value: DecimalValue


class AnnualMetric(BaseModel):
    status: Literal["ready", "not_available", "needs_review"]
    value: DecimalValue | None = None
    variants: list[FilingValue]
    selected_tag: str | None = None
    selected_sources: list[FilingValue] = Field(default_factory=list)
    calculation: str | None = None
    selection_note: str | None = None
    eps_basis: Literal["basic", "diluted"] | None = Field(
        default=None,
        description="Selected EPS basis; null for other metrics or unresolved values.",
    )
    eps_share_basis: EpsShareBasis | None = None
    scope: Literal["total", "continuing"] | None = Field(
        default=None,
        description="OCF scope; null for other metrics or unresolved values.",
    )
    growth: DecimalValue | None = Field(
        default=None,
        description=(
            "Change from the period about a year earlier, both years read from one "
            "annual report (0.05 is 5%); null without such a report or across a loss."
        ),
    )
    growth_report: GrowthReport | None = Field(
        default=None,
        description="The annual report the growth was read from; null without one.",
    )
    series_break: bool = Field(
        default=False,
        description=(
            "The table's values for this and the earlier year imply a change over 5% "
            "away from the report's own, because the definition changed between them."
        ),
    )


class AnnualPeriod(BaseModel):
    fiscal_year: int | None = None
    period_note: str | None = None
    period_start: date
    period_end: date
    metrics: dict[str, AnnualMetric]
