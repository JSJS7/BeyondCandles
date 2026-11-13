"""Financial ratios from the annual values of one period. No database access.

Definitions and limits are in METHODOLOGY.md. A ratio is empty (None) when an input is
missing or the result has no meaning, for example P/E under a loss. It is never filled
with an estimate, an industry average or an earlier year (project-principles.md, 5.2).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from app.automatic_metrics import preferred_deduction
from app.eps_splits import share_count_ratio
from app.policy_files import load_policy
from app.schemas.stock import EpsSplit

PARTICIPATING_PREFERRED_PATH = (
    Path(__file__).resolve().parents[2] / "data/participating-preferred.json"
)
EXCHANGEABLE_UNITS_PATH = (
    Path(__file__).resolve().parents[2] / "data/exchangeable-units.json"
)
OUTSTANDING_SHARES = "outstanding"
WEIGHTED_AVERAGE_SHARES = "weighted_average"
CALCULATED_SHARES = "calculated"
# A cover count is used only within these multiples of the weighted average of the same
# report. Mergers and buybacks moved it from 0.78 (PWR 2015) to 1.65 (TFC 2019) times the
# average; KR's 2015 cover counted 463 million treasury shares too (1.92), and a cover of
# one class of several or in a wrong scale would lie further off (M47).
SMALLEST_COVER_TO_AVERAGE = Decimal("0.5")
LARGEST_COVER_TO_AVERAGE = Decimal("1.75")
# Discontinued operations above this share of net income mark the ratios built on net
# income (M46), the same 5% that makes a difference material in project-principles 5.1.
MATERIAL_DISCONTINUED_SHARE = Decimal("0.05")


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
    # Part of net income; it only marks ratios and enters no calculation.
    discontinued_income: Decimal | None = None
    # Shares outstanding on the report's cover, on the same basis as `shares`.
    shares_outstanding: Decimal | None = None


def has_material_discontinued_operations(values):
    """Whether net income includes discontinued operations above 5% of it (M46).

    Net income and EPS are GAAP and include the result of a business sold or spun off,
    so P/E, ROE, ROA, net margin, OCF / net income and EPS growth can read a one-off
    gain or loss as earnings (JNJ 2023: 62% of net income from Kenvue). A loss counts
    as a gain does, and against a net loss the shares are compared in size.
    """
    if values.net_income is None or values.discontinued_income is None:
        return False
    discontinued_size = abs(values.discontinued_income)
    if discontinued_size == 0:
        return False
    return discontinued_size > abs(values.net_income) * MATERIAL_DISCONTINUED_SHARE


def participating_preferred_tickers():
    """Companies that count their preferred stock, as converted, in their share count."""
    policy = load_policy(PARTICIPATING_PREFERRED_PATH, "participating-preferred")
    return {entry["ticker"] for entry in policy["companies"]}


@dataclass(frozen=True)
class ExchangeableUnits:
    """Units exchangeable one for one into the listed shares, on the share basis of EPS.

    `outside_count` are units the served share count leaves out, `inside_count` units it
    already holds (a diluted count that assumed their exchange).
    """

    outside_count: Decimal
    inside_count: Decimal


def exchangeable_units(split_policy):
    """Return {(ticker, period end): ExchangeableUnits} from data/exchangeable-units.json.

    Counts are stated on the share basis of their filing, so stock splits after the
    filing apply as to share counts. In these companies part of the owners hold units of
    a subsidiary partnership, so the market value of the whole group counts shares and
    units together (M42, M52).
    """
    policy = load_policy(EXCHANGEABLE_UNITS_PATH, "exchangeable-units", versions=(2,))
    result = {}
    for company in policy["companies"]:
        ticker = company["ticker"]
        for entry in company["periods"]:
            filed = date.fromisoformat(entry["filed"])
            pending = [
                EpsSplit.model_validate(action)
                for action in split_policy["actions"]
                if action["ticker"] == ticker
                and filed < date.fromisoformat(action["date"])
            ]
            ratio = share_count_ratio(pending)
            scale = Decimal(ratio.numerator) / Decimal(ratio.denominator)
            units = ExchangeableUnits(
                outside_count=Decimal(entry["units_outside_share_count"]) * scale,
                inside_count=Decimal(entry["units_in_share_count"]) * scale,
            )
            period_end = date.fromisoformat(entry["period_end"])
            result[(ticker, period_end)] = units
    return result


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


def is_plausible_cover_count(cover, average):
    """Whether a cover count lies close enough to the year's weighted average."""
    if cover is None or cover <= 0:
        return False
    ratio = cover / average
    return SMALLEST_COVER_TO_AVERAGE <= ratio <= LARGEST_COVER_TO_AVERAGE


def shares_for_ratios(values, units=None):
    """Return the share count that values the shares, and its basis.

    The count on the report's cover is the closest to the valuation day, as the counts
    of data services are; the weighted average of the year lags a merger or a buyback
    (M47). The average stands in when the cover is missing (several share classes) or
    implausible, and in a period with exchangeable `units`, which are counted against it.

    Companies that tag counts only by class (Visa, Berkshire Hathaway) have neither.
    Their EPS already states the income per converted or equivalent share of the listed
    class, which is the class of the price, so income to common / EPS gives the count.
    """
    if values.shares is not None and values.shares > 0:
        cover = values.shares_outstanding
        if units is None and is_plausible_cover_count(cover, values.shares):
            return cover, OUTSTANDING_SHARES
        return values.shares, WEIGHTED_AVERAGE_SHARES
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


def market_values(price, shares, units):
    """Return (value of the listed shares, value of the whole group).

    Without exchangeable units both are price × shares. With them, the listed shares are
    the count without the units it holds, and the whole group adds the units it leaves out.
    """
    if shares is None:
        return None, None
    if units is None:
        return price * shares, price * shares
    listed_shares = shares - units.inside_count
    all_shares = shares + units.outside_count
    if listed_shares <= 0:
        return None, price * all_shares
    return price * listed_shares, price * all_shares


def price_ratios(values, price, participating, units=None):
    """P/E, P/B and P/S; the price must be on the share basis of EPS and the share count.

    P/B compares the listed shares with equity, which belongs to them; P/S compares the
    whole group with revenue, which is the whole group's, as data services do (M52).
    """
    if price is None:
        return {"pe": None, "pb": None, "ps": None}
    shares, _ = shares_for_ratios(values, units)
    listed_value, group_value = market_values(price, shares, units)
    common = common_equity(values, participating)
    return {
        "pe": divide_by_positive(price, values.eps),
        "pb": divide_by_positive(listed_value, common),
        "ps": divide_by_positive(group_value, values.revenue),
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


def calculate_ratios(values, previous, price, participating=False, units=None):
    """Return every ratio of one period.

    `previous` is the year before (None when unknown). `participating` marks a company
    whose share count already holds its preferred stock as converted. `units` are the
    period's exchangeable units, if any.
    """
    return {
        **price_ratios(values, price, participating, units),
        **return_ratios(values, previous, participating),
        "net_margin": divide_by_positive(values.net_income, values.revenue),
        "operating_margin": divide_by_positive(values.operating_income, values.revenue),
        "ocf_to_net_income": ocf_to_net_income(values),
        "fcf_margin": divide_by_positive(free_cash_flow(values), values.revenue),
        "equity_to_assets": divide_by_positive(values.equity, values.assets),
    }
