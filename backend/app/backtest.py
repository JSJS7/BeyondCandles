"""Annual quintile backtest over the screener's rankings (DEC-33). No database access.

Each year the companies are ranked by one screener ratio as the screener shows it on 31 March,
bought in equal parts at the next session's adjusted close and held without rebalancing until
the next year's purchase session; the last holding ends on 31 Dec 2025. Every quintile, the
benchmark of the ranked companies and the benchmark of the whole catalogue are portfolios
traced through the years, so the cost of each rebalancing follows from the weights the year
before left. The rules and their reasons are in the backtest plan (sections 1 and 2) and,
for readers of the site, in METHODOLOGY.

The loading layer turns the screener's rows and the stored prices into `HoldingPeriod`s;
everything here works on those plain values.
"""

import hashlib
import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import NormalDist

QUINTILE_COUNT = 5
# One-way cost of every trade as a fraction of its value (plan, D8).
TRADE_COST = 0.001
RANDOM_PORTFOLIO_COUNT = 1000
# Fixed, so every run draws the same random portfolios (plan, D7).
BACKTEST_SEED = 20170331
MONTHS_PER_YEAR = 12
# A sale this soon after the last month end (1 or 2 April) joins the month before it, so
# that a holding has twelve months and no stub of a day or two.
SHORTEST_LAST_MONTH = timedelta(days=15)

# True: the lowest values form quintile 1 (a low price for what the company has or earns);
# False: the highest values do. Operating margin is left out: it is empty for whole sectors
# (DEC-17), so its ranking would hold a different set of companies.
RATIO_LOWEST_FIRST = {
    "pe": True,
    "pb": True,
    "ps": True,
    "roe": False,
    "roa": False,
    "net_margin": False,
    "revenue_growth": False,
    "eps_growth": False,
    "ocf_to_net_income": False,
    "fcf_margin": False,
    "equity_to_assets": False,
}

# Cash flow, assets and leverage mean something else for banks, insurers and REITs, so these
# rankings leave out SIC 6000-6999, as the studies of these ratios do (DEC-33).
FINANCIAL_EXCLUDED_RATIOS = frozenset(
    {"fcf_margin", "ocf_to_net_income", "roa", "equity_to_assets"}
)

# The ratios each screener mark covers, as METHODOLOGY describes them. A marked value stays in
# the screener, but the ranking of a covered ratio leaves it out.
SPIN_OFF_RATIOS = frozenset({"pe", "pb", "ps"})
SHARE_ISSUE_RATIOS = frozenset({"pb", "ps"})
DISCONTINUED_RATIOS = frozenset(
    {"pe", "roe", "roa", "net_margin", "ocf_to_net_income", "eps_growth"}
)
PREVIOUS_DISCONTINUED_RATIOS = frozenset({"eps_growth"})


@dataclass(frozen=True)
class Candidate:
    """One company of the catalogue as the screener shows it on a ranking date."""

    ticker: str
    # Ratio name -> value as the screener's API gives it; None means empty, never zero.
    values: dict[str, Decimal | None]
    # Value of the listed shares on the ranking session, the numerator of P/B.
    market_value: Decimal | None = None
    spin_off: bool = False
    share_issue: bool = False
    discontinued_operations: bool = False
    previous_discontinued_operations: bool = False


@dataclass(frozen=True)
class HoldingPeriod:
    """The ranking of one year and the returns of holding each company until the next."""

    ranking_date: date
    buy_session: date
    sell_session: date
    # Every company of the catalogue, also those without a value.
    candidates: list[Candidate]
    # Ticker -> adjusted close of the sell session / of the buy session - 1.
    returns: dict[str, float]
    # Ends before a next ranking (April to December 2025), so it stays out of the summaries.
    partial: bool = False
    # The month ends inside the holding and the sell session (`month_checkpoints`), and for
    # each ticker its adjusted close at each of them / at the buy session. They give the
    # monthly returns that the test of the 1 - 5 spread uses; empty, the test is skipped.
    checkpoints: tuple[date, ...] = ()
    growth: dict[str, tuple[float, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Coverage:
    """How many companies a ranking holds, and why the others are not in it."""

    ranked: int
    financial: int
    empty: int
    flagged: int


@dataclass(frozen=True)
class PortfolioPeriod:
    tickers: tuple[str, ...]
    gross_return: float
    # Sum of all weight changes at the purchase, sales and buys together (1.0 at the start).
    turnover: float
    net_return: float


@dataclass(frozen=True)
class RandomComparison:
    """A portfolio against random portfolios of its size from the same ranked companies."""

    at_least_as_good: int
    count: int

    @property
    def p_value(self):
        """Share of random portfolios that did at least as well, as Burns (2004) counts it."""
        return (self.at_least_as_good + 1) / (self.count + 1)

    @property
    def mid_p_value(self):
        """The centred p-value Burns combines across years with Stouffer's method."""
        return (self.at_least_as_good + 0.5) / (self.count + 1)


@dataclass(frozen=True)
class QuintilePeriod:
    number: int
    portfolio: PortfolioPeriod
    median_market_value: Decimal | None
    random: RandomComparison


@dataclass(frozen=True)
class RatioPeriod:
    ranking_date: date
    buy_session: date
    sell_session: date
    partial: bool
    coverage: Coverage
    quintiles: list[QuintilePeriod]
    # Equal parts of every ranked company: the size-weighted average of the quintiles.
    benchmark: PortfolioPeriod

    @property
    def spread_gross(self):
        return self.quintiles[0].portfolio.gross_return - (
            self.quintiles[-1].portfolio.gross_return
        )

    @property
    def spread_net(self):
        return self.quintiles[0].portfolio.net_return - (
            self.quintiles[-1].portfolio.net_return
        )


@dataclass(frozen=True)
class ReturnSummary:
    """Returns over the full years, compounded and per year."""

    cumulative_gross: float
    cumulative_net: float
    annualised_gross: float
    annualised_net: float


@dataclass(frozen=True)
class QuintileSummary:
    number: int
    returns: ReturnSummary
    # The full years' random comparisons combined with Stouffer's method.
    combined_p_value: float | None


@dataclass(frozen=True)
class RatioResult:
    ratio: str
    lowest_first: bool
    excludes_financial: bool
    periods: list[RatioPeriod]
    quintiles: list[QuintileSummary]
    benchmark: ReturnSummary
    # The test of the ranking: monthly returns of quintile 1 minus quintile 5 over the full
    # years, before costs, as in the studies of portfolio sorts. The mean is per year (x 12);
    # t is the mean divided by its standard error. None without at least two months.
    spread_mean: float | None
    spread_t: float | None
    spread_months: int
    full_years: int
    positive_spread_years: int


@dataclass(frozen=True)
class BacktestResult:
    ratios: list[RatioResult]
    # Equal parts of the whole catalogue, the second line beside each ratio's benchmark.
    catalogue: list[PortfolioPeriod]
    catalogue_summary: ReturnSummary


def flagged_ratios(candidate):
    """The ratios whose value the company's marks leave out of the ranking."""
    ratios = set()
    if candidate.spin_off:
        ratios |= SPIN_OFF_RATIOS
    if candidate.share_issue:
        ratios |= SHARE_ISSUE_RATIOS
    if candidate.discontinued_operations:
        ratios |= DISCONTINUED_RATIOS
    if candidate.previous_discontinued_operations:
        ratios |= PREVIOUS_DISCONTINUED_RATIOS
    return ratios


def ranking(candidates, ratio, financial_tickers):
    """Return the companies ranked by the ratio, best first, and the ranking's coverage.

    A company is left out, in this order, as a financial firm (only for the ratios of
    `FINANCIAL_EXCLUDED_RATIOS`), for an empty value, or for a mark covering the ratio.
    Equal values keep the ticker order in both directions, as the screener's sort does.
    """
    lowest_first = RATIO_LOWEST_FIRST[ratio]
    excludes_financial = ratio in FINANCIAL_EXCLUDED_RATIOS
    ranked = []
    financial = 0
    empty = 0
    flagged = 0
    for candidate in candidates:
        if excludes_financial and candidate.ticker in financial_tickers:
            financial += 1
        elif candidate.values.get(ratio) is None:
            empty += 1
        elif ratio in flagged_ratios(candidate):
            flagged += 1
        else:
            ranked.append(candidate)

    # Python's sort is stable also with reverse=True, so after sorting by ticker first,
    # companies with equal values stay in ticker order.
    ranked.sort(key=ticker_of)
    ranked.sort(key=lambda candidate: candidate.values[ratio], reverse=not lowest_first)
    coverage = Coverage(
        ranked=len(ranked), financial=financial, empty=empty, flagged=flagged
    )
    return ranked, coverage


def ticker_of(candidate):
    return candidate.ticker


def quintile_sizes(count):
    """Equal quintiles; the first `count mod 5` hold one company more (171 -> 35, 34, ...)."""
    if count < QUINTILE_COUNT:
        raise ValueError(
            f"{count} ranked companies cannot form {QUINTILE_COUNT} quintiles"
        )
    base_size, remainder = divmod(count, QUINTILE_COUNT)
    sizes = []
    for position in range(QUINTILE_COUNT):
        if position < remainder:
            sizes.append(base_size + 1)
        else:
            sizes.append(base_size)
    return sizes


def split_quintiles(ranked):
    quintiles = []
    start = 0
    for size in quintile_sizes(len(ranked)):
        quintiles.append(ranked[start : start + size])
        start += size
    return quintiles


def period_return(buy_close, sell_close):
    return sell_close / buy_close - 1


def mean_return(tickers, returns):
    """Return of equal parts bought at the start and held: the plain mean.

    `math.fsum` rounds the sum exactly, so the same companies in any order give the same
    number, and a random portfolio equal to the quintile counts as at least as good.
    """
    total = math.fsum(returns[ticker] for ticker in tickers)
    return total / len(tickers)


def drifted_weights(tickers, returns):
    """Weights of equal parts at the end of the holding: each grew with its own return."""
    growth = {ticker: 1 + returns[ticker] for ticker in tickers}
    total = math.fsum(growth.values())
    weights = {}
    for ticker, value in growth.items():
        weights[ticker] = value / total
    return weights


def turnover(previous_weights, tickers):
    """Sum of weight changes from the drifted weights to new equal parts.

    Companies leaving are sold, new ones bought and staying ones brought back to an equal
    part; each trade counts once. The first purchase, from cash, has a turnover of 1.
    """
    if not previous_weights:
        return 1.0
    new_weight = 1 / len(tickers)
    new_weights = {ticker: new_weight for ticker in tickers}
    changes = []
    for ticker in set(previous_weights) | set(new_weights):
        before = previous_weights.get(ticker, 0.0)
        after = new_weights.get(ticker, 0.0)
        changes.append(abs(after - before))
    return math.fsum(changes)


def net_return(gross_return, traded):
    """The cost is paid at the purchase from the portfolio's value; the final sale is free."""
    return (1 - TRADE_COST * traded) * (1 + gross_return) - 1


def portfolio_periods(holdings, periods):
    """Trace one portfolio: `holdings[i]` are the tickers bought for `periods[i]`."""
    results = []
    previous_weights = {}
    for tickers, period in zip(holdings, periods, strict=True):
        traded = turnover(previous_weights, tickers)
        gross = mean_return(tickers, period.returns)
        results.append(
            PortfolioPeriod(
                tickers=tuple(tickers),
                gross_return=gross,
                turnover=traded,
                net_return=net_return(gross, traded),
            )
        )
        previous_weights = drifted_weights(tickers, period.returns)
    return results


def draw_seed(*parts):
    """A seed of its own for each draw, so a result does not depend on what ran before it."""
    text = ":".join(str(part) for part in (BACKTEST_SEED, *parts))
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def random_mean_returns(pool_returns, size, rng, count=RANDOM_PORTFOLIO_COUNT):
    """Mean returns of `count` random portfolios of `size` companies from the pool.

    Each draw is a partial Fisher-Yates shuffle on `rng.random()`, because Python promises the
    same sequence across versions only for `random()` with the same seed, not for `sample`
    or `shuffle` (plan, D7).
    """
    pool_size = len(pool_returns)
    means = []
    for _ in range(count):
        pool = list(pool_returns)
        for position in range(size):
            remaining = pool_size - position
            chosen = position + int(rng.random() * remaining)
            pool[position], pool[chosen] = pool[chosen], pool[position]
        means.append(math.fsum(pool[:size]) / size)
    return means


def compare_with_random(portfolio_return, pool_returns, size, seed):
    rng = random.Random(seed)
    means = random_mean_returns(pool_returns, size, rng)
    at_least_as_good = 0
    for mean in means:
        if mean >= portfolio_return:
            at_least_as_good += 1
    return RandomComparison(at_least_as_good=at_least_as_good, count=len(means))


def stouffer(mid_p_values):
    """Combine one-sided p-values of independent years into one (Burns 2004, section 3).

    Stouffer rather than Fisher: Fisher would call a portfolio that swings between years
    both better and worse than chance.
    """
    if not mid_p_values:
        return None
    normal = NormalDist()
    scores = [normal.inv_cdf(1 - p_value) for p_value in mid_p_values]
    combined_score = math.fsum(scores) / math.sqrt(len(scores))
    return 1 - normal.cdf(combined_score)


def return_summary(portfolio):
    """Compounded and per-year returns of the full years of one traced portfolio."""
    gross_growth = math.prod(1 + period.gross_return for period in portfolio)
    net_growth = math.prod(1 + period.net_return for period in portfolio)
    years = len(portfolio)
    return ReturnSummary(
        cumulative_gross=gross_growth - 1,
        cumulative_net=net_growth - 1,
        annualised_gross=gross_growth ** (1 / years) - 1,
        annualised_net=net_growth ** (1 / years) - 1,
    )


def median_market_value(members):
    values = [
        member.market_value for member in members if member.market_value is not None
    ]
    if not values:
        return None
    return statistics.median(values)


def check_periods(periods):
    """Holdings must follow each other, and every company needs a return in each."""
    if not periods:
        raise ValueError("The backtest needs at least one holding period")
    for earlier, later in pairwise(periods):
        if earlier.partial:
            raise ValueError(f"Partial period {earlier.ranking_date} is not the last")
        if earlier.sell_session != later.buy_session:
            raise ValueError(
                f"Holding from {earlier.buy_session} ends on {earlier.sell_session}, "
                f"not on the next purchase {later.buy_session}"
            )
    for period in periods:
        for candidate in period.candidates:
            if candidate.ticker not in period.returns:
                raise ValueError(
                    f"{candidate.ticker} has no return from {period.buy_session}"
                )
            if not period.checkpoints:
                continue
            path = period.growth.get(candidate.ticker)
            if path is None or len(path) != len(period.checkpoints):
                raise ValueError(
                    f"{candidate.ticker} lacks a month-end close from {period.buy_session}"
                )


def month_checkpoints(sessions, buy_session, sell_session):
    """The last session of every month inside the holding, then the sell session.

    `sessions` are all trading days in order. A sale soon after the last month end, as on
    1 April, joins the month before it, so a year's holding has twelve monthly returns.
    """
    inside = [session for session in sessions if buy_session < session < sell_session]
    month_ends = []
    for index, session in enumerate(inside):
        is_last = index + 1 == len(inside)
        if is_last or inside[index + 1].month != session.month:
            month_ends.append(session)
    if month_ends and sell_session - month_ends[-1] < SHORTEST_LAST_MONTH:
        month_ends.pop()
    return (*month_ends, sell_session)


def monthly_returns(tickers, period):
    """Monthly returns of equal parts bought at the start of the holding and held.

    The portfolio's value at each checkpoint is the mean growth of its companies, so the
    weights drift with prices, as in the yearly return.
    """
    returns = []
    previous_value = 1.0
    for position in range(len(period.checkpoints)):
        value = math.fsum(period.growth[ticker][position] for ticker in tickers)
        value /= len(tickers)
        returns.append(value / previous_value - 1)
        previous_value = value
    return returns


def t_statistic(values):
    """Mean divided by its standard error; None without two values or any variation."""
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    return statistics.fmean(values) / (deviation / math.sqrt(len(values)))


def spread_test(periods, quintile_portfolios):
    """Monthly quintile 1 - 5 returns over the full years: (mean per year, t, months)."""
    spreads = []
    for index, period in enumerate(periods):
        if period.partial or not period.checkpoints:
            continue
        first = monthly_returns(quintile_portfolios[0][index].tickers, period)
        last = monthly_returns(quintile_portfolios[-1][index].tickers, period)
        for first_return, last_return in zip(first, last, strict=True):
            spreads.append(first_return - last_return)
    if not spreads:
        return None, None, 0
    mean_per_year = statistics.fmean(spreads) * MONTHS_PER_YEAR
    return mean_per_year, t_statistic(spreads), len(spreads)


def backtest_ratio(ratio, periods, financial_tickers):
    rankings = []
    coverages = []
    for period in periods:
        ranked, coverage = ranking(period.candidates, ratio, financial_tickers)
        rankings.append(ranked)
        coverages.append(coverage)

    quintile_members = [split_quintiles(ranked) for ranked in rankings]
    quintile_portfolios = []
    for position in range(QUINTILE_COUNT):
        holdings = []
        for members in quintile_members:
            holdings.append([member.ticker for member in members[position]])
        quintile_portfolios.append(portfolio_periods(holdings, periods))
    benchmark_holdings = []
    for ranked in rankings:
        benchmark_holdings.append([member.ticker for member in ranked])
    benchmark = portfolio_periods(benchmark_holdings, periods)

    ratio_periods = []
    for index, period in enumerate(periods):
        # Pool in ticker order, so the draws do not depend on how the ranking sorted it.
        pool_tickers = sorted(member.ticker for member in rankings[index])
        pool_returns = [period.returns[ticker] for ticker in pool_tickers]
        quintiles = []
        for position in range(QUINTILE_COUNT):
            portfolio = quintile_portfolios[position][index]
            number = position + 1
            seed = draw_seed(ratio, period.ranking_date.isoformat(), number)
            comparison = compare_with_random(
                portfolio.gross_return, pool_returns, len(portfolio.tickers), seed
            )
            quintiles.append(
                QuintilePeriod(
                    number=number,
                    portfolio=portfolio,
                    median_market_value=median_market_value(
                        quintile_members[index][position]
                    ),
                    random=comparison,
                )
            )
        ratio_periods.append(
            RatioPeriod(
                ranking_date=period.ranking_date,
                buy_session=period.buy_session,
                sell_session=period.sell_session,
                partial=period.partial,
                coverage=coverages[index],
                quintiles=quintiles,
                benchmark=benchmark[index],
            )
        )
    return ratio_result(ratio, periods, ratio_periods, quintile_portfolios, benchmark)


def ratio_result(ratio, periods, ratio_periods, quintile_portfolios, benchmark):
    """Summaries over the full years only; the partial period is shown on its own."""
    full = [index for index, period in enumerate(periods) if not period.partial]

    quintile_summaries = []
    for position, portfolio in enumerate(quintile_portfolios):
        full_portfolio = [portfolio[index] for index in full]
        mid_p_values = []
        for index in full:
            mid_p_values.append(
                ratio_periods[index].quintiles[position].random.mid_p_value
            )
        quintile_summaries.append(
            QuintileSummary(
                number=position + 1,
                returns=return_summary(full_portfolio),
                combined_p_value=stouffer(mid_p_values),
            )
        )

    positive_spread_years = 0
    for index in full:
        if ratio_periods[index].spread_gross > 0:
            positive_spread_years += 1
    spread_mean, spread_t, spread_months = spread_test(periods, quintile_portfolios)
    return RatioResult(
        ratio=ratio,
        lowest_first=RATIO_LOWEST_FIRST[ratio],
        excludes_financial=ratio in FINANCIAL_EXCLUDED_RATIOS,
        periods=ratio_periods,
        quintiles=quintile_summaries,
        benchmark=return_summary([benchmark[index] for index in full]),
        spread_mean=spread_mean,
        spread_t=spread_t,
        spread_months=spread_months,
        full_years=len(full),
        positive_spread_years=positive_spread_years,
    )


def run_backtest(periods, financial_tickers, ratios=None):
    """Every ratio's backtest over the same holding periods, and the catalogue's benchmark."""
    check_periods(periods)
    full_periods = [period for period in periods if not period.partial]
    if not full_periods:
        raise ValueError("The backtest needs at least one full year")
    if ratios is None:
        ratios = list(RATIO_LOWEST_FIRST)

    results = []
    for ratio in ratios:
        results.append(backtest_ratio(ratio, periods, financial_tickers))

    catalogue_holdings = []
    for period in periods:
        catalogue_holdings.append([candidate.ticker for candidate in period.candidates])
    catalogue = portfolio_periods(catalogue_holdings, periods)
    full_catalogue = []
    for period, portfolio in zip(periods, catalogue, strict=True):
        if not period.partial:
            full_catalogue.append(portfolio)
    return BacktestResult(
        ratios=results,
        catalogue=catalogue,
        catalogue_summary=return_summary(full_catalogue),
    )
