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


class SpinOff(BaseModel):
    """A business separated after the period: the price excludes it, the period does not."""

    event_date: date
    # The part of the price the business held, from the vendor's ratio (0.06 is 6%).
    separated_fraction: DecimalValue


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
    # Empty unless P/E, P/B and P/S join a price after a spin-off with earlier results.
    spin_offs: list[SpinOff] = []
    # Part of the owners hold units exchangeable into the listed shares: P/S counts shares
    # and units, P/B only the listed shares (data/exchangeable-units.json, M52).
    exchangeable_units: bool = False
    # Net income includes discontinued operations above 5% of it, so the ratios built on
    # net income or EPS can read a one-off result as earnings (M46).
    discontinued_operations: bool = False
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
