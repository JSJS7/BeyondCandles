"""The backtest endpoints: load the screener's rankings and the prices, then run app.backtest.

Each ranking is the screener's own answer for 31 March (`get_screener`), so the backtest
uses exactly the values, marks and empty cells the screener shows that day, and every
quintile can be looked up there. Purchases and sales use the adjusted close: the
purchase on the session after the ranking session, the sale on the next purchase session
or, for the last holding, on the last session of the data window.
"""

import threading
from dataclasses import replace
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.backtest import (
    BACKTEST_SEED,
    RANDOM_PORTFOLIO_COUNT,
    RATIO_LOWEST_FIRST,
    TRADE_COST,
    Candidate,
    HoldingPeriod,
    month_checkpoints,
    period_return,
    run_backtest,
)
from app.database import get_connection
from app.models import Company, DailyPrice
from app.schemas import backtest as schemas
from app.screener import LATEST_VALUATION_DATE, get_screener
from app.sic_codes import financial_tickers

router = APIRouter(prefix="/backtest", tags=["backtest"])
DatabaseConnection = Annotated[Connection, Depends(get_connection)]

# 31 March of 2017-2025: every ratio starts in 2017, when ROE, ROA and both growth rates
# first have values for most companies (plan, D5).
RANKING_DATES = tuple(date(year, 3, 31) for year in range(2017, 2026))
END_DATE = LATEST_VALUATION_DATE
RETURN_DECIMALS = 6
# Database engine -> (periods, result) of `cached_backtest`; tests clear it, because they
# change the data between requests.
CACHED_RESULTS = {}
CACHE_LOCK = threading.Lock()


def last_session(connection, day):
    """The last trading session on or before the day."""
    prices = DailyPrice.__table__
    session = connection.execute(
        select(func.max(prices.c.date)).where(prices.c.date <= day)
    ).scalar_one()
    if session is None:
        raise ValueError(f"No trading session on or before {day}")
    return session


def next_session(connection, day):
    """The first trading session after the day."""
    prices = DailyPrice.__table__
    session = connection.execute(
        select(func.min(prices.c.date)).where(prices.c.date > day)
    ).scalar_one()
    if session is None:
        raise ValueError(f"No trading session after {day}")
    return session


def adjusted_closes(connection, first, last):
    """{(ticker, session): adjusted close} for every company and session from first to last.

    Every session, not only the month ends: the worst drop follows the daily value.
    """
    prices = DailyPrice.__table__
    companies = Company.__table__
    query = (
        select(companies.c.ticker, prices.c.date, prices.c.adjusted_close)
        .join(companies, companies.c.id == prices.c.company_id)
        .where(prices.c.date >= first, prices.c.date <= last)
    )
    closes = {}
    for row in connection.execute(query).mappings():
        closes[(row["ticker"], row["date"])] = float(row["adjusted_close"])
    return closes


def candidate_from_row(row):
    """One screener row as the backtest reads it; a company without a report has no values."""
    if row.latest is None:
        empty_values = {ratio: None for ratio in RATIO_LOWEST_FIRST}
        return Candidate(ticker=row.ticker, values=empty_values)
    latest = row.latest
    # Read the attributes, not model_dump(): DecimalValue serialises to text there, and
    # text sorts "11.3" before "3.59".
    values = {}
    for ratio in RATIO_LOWEST_FIRST:
        values[ratio] = getattr(latest.ratios, ratio)
    return Candidate(
        ticker=row.ticker,
        values=values,
        market_value=latest.market_value,
        spin_off=len(latest.spin_offs) > 0,
        share_issue=latest.share_issue,
        discontinued_operations=latest.discontinued_operations,
        previous_discontinued_operations=latest.previous_discontinued_operations,
    )


def trading_sessions(connection, first, last):
    """Every trading day from `first` to `last`, in order."""
    prices = DailyPrice.__table__
    query = (
        select(prices.c.date)
        .where(prices.c.date >= first, prices.c.date <= last)
        .distinct()
        .order_by(prices.c.date)
    )
    return list(connection.execute(query).scalars())


def holding_returns(tickers, closes, buy_session, sell_session):
    """Returns of the companies with both closes; a missing one stops `run_backtest`."""
    returns = {}
    for ticker in tickers:
        buy_close = closes.get((ticker, buy_session))
        sell_close = closes.get((ticker, sell_session))
        if buy_close is None or sell_close is None:
            continue
        returns[ticker] = period_return(buy_close, sell_close)
    return returns


def growth_paths(tickers, closes, buy_session, checkpoints):
    """Each ticker's close at every checkpoint / its buy close; incomplete ones are left out
    and stop `run_backtest`."""
    paths = {}
    for ticker in tickers:
        buy_close = closes.get((ticker, buy_session))
        path = [closes.get((ticker, checkpoint)) for checkpoint in checkpoints]
        if buy_close is None or None in path:
            continue
        paths[ticker] = tuple(close / buy_close for close in path)
    return paths


def load_periods(connection, ranking_dates, end_date):
    """One holding period per ranking date; the last one ends on the window's last session."""
    buy_sessions = []
    for ranking_date in ranking_dates:
        ranking_session = last_session(connection, ranking_date)
        buy_sessions.append(next_session(connection, ranking_session))
    final_session = last_session(connection, end_date)
    sell_sessions = [*buy_sessions[1:], final_session]
    sessions = trading_sessions(connection, buy_sessions[0], final_session)
    checkpoints = []
    holding_sessions = []
    for buy_session, sell_session in zip(buy_sessions, sell_sessions, strict=True):
        checkpoints.append(month_checkpoints(sessions, buy_session, sell_session))
        inside = []
        for session in sessions:
            if buy_session < session <= sell_session:
                inside.append(session)
        holding_sessions.append(tuple(inside))
    closes = adjusted_closes(connection, buy_sessions[0], final_session)

    periods = []
    last_index = len(ranking_dates) - 1
    for index, ranking_date in enumerate(ranking_dates):
        if buy_sessions[index] > final_session:
            raise ValueError(f"The purchase after {ranking_date} is after {end_date}")
        screener = get_screener(connection, ranking_date)
        candidates = [candidate_from_row(row) for row in screener.rows]
        tickers = [candidate.ticker for candidate in candidates]
        periods.append(
            HoldingPeriod(
                ranking_date=ranking_date,
                buy_session=buy_sessions[index],
                sell_session=sell_sessions[index],
                candidates=candidates,
                returns=holding_returns(
                    tickers, closes, buy_sessions[index], sell_sessions[index]
                ),
                partial=index == last_index,
                checkpoints=checkpoints[index],
                growth=growth_paths(
                    tickers, closes, buy_sessions[index], checkpoints[index]
                ),
                sessions=holding_sessions[index],
                daily_growth=growth_paths(
                    tickers, closes, buy_sessions[index], holding_sessions[index]
                ),
            )
        )
    return periods


def rounded(value):
    return round(value, RETURN_DECIMALS)


def optional_rounded(value):
    if value is None:
        return None
    return rounded(value)


def portfolio_fields(portfolio):
    return {
        "count": len(portfolio.tickers),
        "gross_return": rounded(portfolio.gross_return),
        "net_return": rounded(portfolio.net_return),
        "turnover": rounded(portfolio.turnover),
    }


def summary_fields(summary):
    return {
        "cumulative_gross": rounded(summary.cumulative_gross),
        "cumulative_net": rounded(summary.cumulative_net),
        "annualised_gross": rounded(summary.annualised_gross),
        "annualised_net": rounded(summary.annualised_net),
        "volatility": optional_rounded(summary.volatility),
        "worst_drop": optional_rounded(summary.worst_drop),
    }


def quintile_members(ratio, quintile, period):
    candidates = {candidate.ticker: candidate for candidate in period.candidates}
    members = []
    for ticker in quintile.portfolio.tickers:
        candidate = candidates[ticker]
        members.append(
            schemas.QuintileMember(
                ticker=ticker,
                value=candidate.values[ratio],
                period_return=rounded(period.returns[ticker]),
                market_value=candidate.market_value,
            )
        )
    return members


def ratio_response(result, periods, with_members):
    response_periods = []
    for ratio_period, period in zip(result.periods, periods, strict=True):
        quintiles = []
        for quintile in ratio_period.quintiles:
            members = None
            if with_members:
                members = quintile_members(result.ratio, quintile, period)
            quintiles.append(
                schemas.QuintilePeriod(
                    **portfolio_fields(quintile.portfolio),
                    number=quintile.number,
                    median_market_value=quintile.median_market_value,
                    p_value=quintile.random.p_value,
                    members=members,
                )
            )
        coverage = ratio_period.coverage
        response_periods.append(
            schemas.RatioPeriod(
                ranking_date=ratio_period.ranking_date,
                buy_session=ratio_period.buy_session,
                sell_session=ratio_period.sell_session,
                partial=ratio_period.partial,
                coverage=schemas.Coverage(
                    ranked=coverage.ranked,
                    financial=coverage.financial,
                    empty=coverage.empty,
                    flagged=coverage.flagged,
                ),
                quintiles=quintiles,
                benchmark=schemas.PortfolioPeriod(
                    **portfolio_fields(ratio_period.benchmark)
                ),
                spread_gross=rounded(ratio_period.spread_gross),
                spread_net=rounded(ratio_period.spread_net),
            )
        )

    quintile_summaries = []
    for summary in result.quintiles:
        quintile_summaries.append(
            schemas.QuintileSummary(
                **summary_fields(summary.returns),
                number=summary.number,
                combined_p_value=summary.combined_p_value,
            )
        )
    return schemas.RatioBacktest(
        ratio=result.ratio,
        lowest_first=result.lowest_first,
        excludes_financial=result.excludes_financial,
        full_years=result.full_years,
        positive_spread_years=result.positive_spread_years,
        spread_mean=optional_rounded(result.spread_mean),
        spread_t=optional_rounded(result.spread_t),
        spread_months=result.spread_months,
        quintiles=quintile_summaries,
        benchmark=schemas.ReturnSummary(**summary_fields(result.benchmark)),
        periods=response_periods,
    )


def backtest_response(result, periods, with_members):
    catalogue = []
    for portfolio, period in zip(result.catalogue, periods, strict=True):
        catalogue.append(
            schemas.CataloguePeriod(
                **portfolio_fields(portfolio),
                ranking_date=period.ranking_date,
                buy_session=period.buy_session,
                sell_session=period.sell_session,
                partial=period.partial,
            )
        )
    ratios = []
    for ratio_result in result.ratios:
        ratios.append(ratio_response(ratio_result, periods, with_members))
    return schemas.BacktestResponse(
        trade_cost=TRADE_COST,
        random_portfolios=RANDOM_PORTFOLIO_COUNT,
        seed=BACKTEST_SEED,
        catalogue=catalogue,
        catalogue_summary=schemas.ReturnSummary(
            **summary_fields(result.catalogue_summary)
        ),
        ratios=ratios,
    )


def cached_backtest(connection):
    """(periods, result) of every ratio, computed once per database engine.

    One run takes about 7 s (25.11.2026: 4.4 s to load the nine rankings and the daily
    closes, 2.8 s to compute), and the data change only with a manual rebuild. After a
    rebuild the API must be restarted to show the new result.

    The page asks for the summary and one ratio at the same time, and FastAPI answers both
    in parallel threads. The lock makes the second request wait for the first one's result
    instead of computing it again (both took 8.3 s instead of 5 s without it).
    """
    engine = connection.engine
    with CACHE_LOCK:
        cached = CACHED_RESULTS.get(engine)
        if cached is None:
            periods = load_periods(connection, RANKING_DATES, END_DATE)
            result = run_backtest(periods, financial_tickers())
            cached = (periods, result)
            CACHED_RESULTS[engine] = cached
    return cached


@router.get("", response_model=schemas.BacktestResponse)
def get_backtest(connection: DatabaseConnection):
    """Every ratio's quintiles by year, without the companies in them."""
    periods, result = cached_backtest(connection)
    return backtest_response(result, periods, with_members=False)


@router.get("/{ratio}", response_model=schemas.BacktestResponse)
def get_ratio_backtest(ratio: str, connection: DatabaseConnection):
    """One ratio's quintiles by year, with each company's value, return and market value."""
    if ratio not in RATIO_LOWEST_FIRST:
        known = ", ".join(RATIO_LOWEST_FIRST)
        raise HTTPException(404, detail=f"Unknown ratio {ratio}; known: {known}.")
    periods, result = cached_backtest(connection)
    selected = []
    for ratio_result in result.ratios:
        if ratio_result.ratio == ratio:
            selected.append(ratio_result)
    one_ratio = replace(result, ratios=selected)
    return backtest_response(one_ratio, periods, with_members=True)
