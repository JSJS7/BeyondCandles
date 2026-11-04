from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.stock import DecimalValue


class Ratios(BaseModel):
    """Every value is a plain ratio (0.25 is 25%); null means empty, never zero."""

    pe: DecimalValue | None = None
    pb: DecimalValue | None = None
    ps: DecimalValue | None = None
    roe: DecimalValue | None = None
    roa: DecimalValue | None = None
    net_margin: DecimalValue | None = None
    operating_margin: DecimalValue | None = None
    revenue_growth: DecimalValue | None = None
    eps_growth: DecimalValue | None = None
    ocf_to_net_income: DecimalValue | None = None
    fcf_margin: DecimalValue | None = None
    equity_to_assets: DecimalValue | None = None


class PeriodRatios(BaseModel):
    """One annual period valued on one session, with what is needed to read its ratios."""

    period_start: date
    period_end: date
    price_date: date | None = None
    price: DecimalValue | None = None
    eps_basis: Literal["basic", "diluted"] | None = None
    ocf_scope: Literal["total", "continuing"] | None = None
    shares_basis: Literal["reported", "calculated"] | None = None
    revenue_basis_break: bool = False
    eps_basis_break: bool = False
    ratios: Ratios


class ScreenerRow(BaseModel):
    ticker: str
    name: str
    sector: str | None = None
    # Null only when the company has no annual report known on the valuation date.
    latest: PeriodRatios | None = None


class ScreenerResponse(BaseModel):
    valuation_date: date
    rows: list[ScreenerRow]
