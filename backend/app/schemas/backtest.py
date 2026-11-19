"""The annual quintile backtest (DEC-33) as the API returns it.

Returns, turnover and p-values are plain fractions (0.25 is 25%) as JSON numbers: they are
computed from the vendor's adjusted closes, which are floating point anyway. Returns and
turnover are rounded to six decimals; p-values are not, so a very small one never reads 0.
Ratio values and market values are decimal text, as in the screener.
"""

from datetime import date

from pydantic import BaseModel

from app.schemas.stock import DecimalValue


class Coverage(BaseModel):
    """Companies in the ranking, and why the others are not in it."""

    ranked: int
    # SIC 6000-6999, only for FCF margin, OCF / net income, ROA and equity / assets.
    financial: int
    empty: int
    # Marked Spin-off, Share issue or Discontinued ops for this ratio.
    flagged: int


class PortfolioPeriod(BaseModel):
    count: int
    gross_return: float
    net_return: float
    # Sum of weight changes at the purchase, sales and buys together (1 at the start).
    turnover: float


class QuintileMember(BaseModel):
    ticker: str
    value: DecimalValue
    period_return: float
    market_value: DecimalValue | None = None


class QuintilePeriod(PortfolioPeriod):
    number: int
    median_market_value: DecimalValue | None = None
    # Share of 1000 random portfolios of this size from the ranked companies that did at
    # least as well before costs, (n + 1) / 1001.
    p_value: float
    # Null in GET /backtest; listed in GET /backtest/{ratio}.
    members: list[QuintileMember] | None = None


class RatioPeriod(BaseModel):
    ranking_date: date
    buy_session: date
    sell_session: date
    # April to December 2025: shown alone, outside the summaries.
    partial: bool
    coverage: Coverage
    quintiles: list[QuintilePeriod]
    # Equal parts of every ranked company.
    benchmark: PortfolioPeriod
    spread_gross: float
    spread_net: float


class ReturnSummary(BaseModel):
    """Over the full years: compounded, and per year (geometric mean)."""

    cumulative_gross: float
    cumulative_net: float
    annualised_gross: float
    annualised_net: float


class QuintileSummary(ReturnSummary):
    number: int
    # The full years' p-values combined with Stouffer's method (Burns 2004).
    combined_p_value: float | None = None


class RatioBacktest(BaseModel):
    ratio: str
    # True: quintile 1 holds the lowest values (P/E, P/B, P/S).
    lowest_first: bool
    excludes_financial: bool
    full_years: int
    positive_spread_years: int
    # The test of the ranking, before costs: monthly quintile 1 - 5 returns over the full
    # years, their mean per year (x 12) and t, the mean over its standard error.
    spread_mean: float | None = None
    spread_t: float | None = None
    spread_months: int
    quintiles: list[QuintileSummary]
    benchmark: ReturnSummary
    periods: list[RatioPeriod]


class CataloguePeriod(PortfolioPeriod):
    ranking_date: date
    buy_session: date
    sell_session: date
    partial: bool


class BacktestResponse(BaseModel):
    trade_cost: float
    random_portfolios: int
    seed: int
    # Equal parts of the whole catalogue, the same for every ratio.
    catalogue: list[CataloguePeriod]
    catalogue_summary: ReturnSummary
    ratios: list[RatioBacktest]
