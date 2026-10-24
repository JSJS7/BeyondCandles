from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer


def decimal_string(value: Decimal) -> str:
    """Keep every decimal digit and avoid scientific notation in HTTP responses."""
    return format(value, "f")


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


class AnnualPeriod(BaseModel):
    fiscal_year: int | None = None
    period_note: str | None = None
    period_start: date
    period_end: date
    metrics: dict[str, AnnualMetric]
