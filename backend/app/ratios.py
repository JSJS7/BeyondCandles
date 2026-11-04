"""Financial ratios from the annual values of one period. No database access.

Definitions and limits are in METHODOLOGY.md. A ratio is empty (None) when an input is
missing or the result has no meaning, for example P/E under a loss. It is never filled
with an estimate, an industry average or an earlier year (project-principles.md, 5.2).
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from app.automatic_metrics import preferred_deduction
from app.policy_files import load_policy

PARTICIPATING_PREFERRED_PATH = (
    Path(__file__).resolve().parents[2] / "data/participating-preferred.json"
)
REPORTED_SHARES = "reported"
CALCULATED_SHARES = "calculated"


@dataclass(frozen=True)
class PeriodValues:
    """The values of one annual period; a per-share basis is shared by EPS and shares."""

    revenue: Decimal | None
    net_income: Decimal | None
    eps: Decimal | None
    equity: Decimal | None
    operating_cash_flow: Decimal | None
    ocf_scope: Literal["total", "continuing"] | None
    shares: Decimal | None
    assets: Decimal | None
    capex: Decimal | None
    operating_income: Decimal | None
    preferred_value: Decimal | None
    preferred_dividends: Decimal | None


def participating_preferred_tickers():
    """Companies that count their preferred stock, as converted, in their share count."""
    policy = load_policy(PARTICIPATING_PREFERRED_PATH, "participating-preferred")
    return {entry["ticker"] for entry in policy["companies"]}


def income_to_common(values):
    """Net income after preferred dividends, the income that EPS divides.

    Negative "dividends" are gains on redeeming preferred stock (GM 2024). They raise this
    income as they raise reported EPS, so the two remain consistent.
    """
    if values.net_income is None:
        return None
    dividends = values.preferred_dividends
    if dividends is None:
        return values.net_income
    return values.net_income - dividends


def shares_for_ratios(values):
    """Return the share count and its basis: the tag, otherwise income to common / EPS.

    Companies with several share classes tag counts only by class (Visa, Berkshire
    Hathaway), so no total exists. Their EPS already states the income per converted or
    equivalent share of the listed class, which is the class of the price.
    """
    if values.shares is not None and values.shares > 0:
        return values.shares, REPORTED_SHARES
    income = income_to_common(values)
    if income is None or not values.eps:
        return None, None
    calculated = income / values.eps
    if calculated <= 0:
        return None, None
    return calculated, CALCULATED_SHARES


def common_equity(values, participating=False):
    """Equity of common holders, or None when the preferred amount to deduct is unknown."""
    if values.equity is None:
        return None
    if participating:
        return values.equity
    deduction = preferred_deduction(
        values.preferred_value, values.preferred_dividends, values.equity
    )
    if deduction is None:
        return None
    return values.equity - deduction


def divide_by_positive(numerator, denominator):
    """A ratio whose denominator must be positive to mean anything."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def average_of_positive(first, second):
    """The average of two balances; empty unless both are known and positive."""
    if first is None or second is None or first <= 0 or second <= 0:
        return None
    return (first + second) / 2


def price_ratios(values, price, participating):
    """P/E, P/B and P/S; the price must be on the share basis of EPS and the share count."""
    if price is None:
        return {"pe": None, "pb": None, "ps": None}
    shares, _ = shares_for_ratios(values)
    market_value = None if shares is None else price * shares
    common = common_equity(values, participating)
    return {
        "pe": divide_by_positive(price, values.eps),
        "pb": divide_by_positive(market_value, common),
        "ps": divide_by_positive(market_value, values.revenue),
    }


def return_ratios(values, previous, participating):
    """ROE and ROA on the average of this and the earlier year's closing balance."""
    if previous is None:
        return {"roe": None, "roa": None}
    current_common = common_equity(values, participating)
    previous_common = common_equity(previous, participating)
    average_common = average_of_positive(current_common, previous_common)
    average_assets = average_of_positive(values.assets, previous.assets)
    return {
        "roe": divide_by_positive(income_to_common(values), average_common),
        "roa": divide_by_positive(values.net_income, average_assets),
    }


def free_cash_flow(values):
    if values.operating_cash_flow is None or values.capex is None:
        return None
    return values.operating_cash_flow - values.capex


def ocf_to_net_income(values):
    """Empty for continuing cash flow, which does not match total net income in scope."""
    if values.ocf_scope != "total":
        return None
    return divide_by_positive(values.operating_cash_flow, values.net_income)


def calculate_ratios(values, previous, price, participating=False):
    """Return every ratio of one period.

    `previous` is the year before (None when unknown). `participating` marks a company
    whose share count already holds its preferred stock as converted.
    """
    return {
        **price_ratios(values, price, participating),
        **return_ratios(values, previous, participating),
        "net_margin": divide_by_positive(values.net_income, values.revenue),
        "operating_margin": divide_by_positive(values.operating_income, values.revenue),
        "ocf_to_net_income": ocf_to_net_income(values),
        "fcf_margin": divide_by_positive(free_cash_flow(values), values.revenue),
        "equity_to_assets": divide_by_positive(values.equity, values.assets),
    }
