"""Read the published company catalogue, prices and reported annual financials."""

import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.annual_financials import (
    METRIC_NAMES,
    annual_periods,
    reviewed_facts_query,
)
from app.database import get_connection
from app.eps_splits import normalize_eps
from app.fiscal_periods import fiscal_rules
from app.growth import add_table_growth
from app.metric_history import HistoryOutOfDate, periods_as_of
from app.models import AnnualMetricVersion, Company, DailyPrice
from app.schemas.stock import AnnualPeriod, Price, Stock

router = APIRouter(prefix="/stocks", tags=["stocks"])
DatabaseConnection = Annotated[Connection, Depends(get_connection)]

# Catalogue tickers are letters with an optional class suffix (BRK-B); exchanges allow a dot.
LONGEST_TICKER = 10
TICKER_PATTERN = re.compile(r"[A-Za-z0-9.\-]+")


def find_company(connection, ticker):
    """The published company, or 404 for any ticker outside the catalogue.

    The reply does not repeat the input (N107): a path can be arbitrarily long text, and a
    text that cannot be a ticker is not looked up at all.
    """
    not_found = HTTPException(404, detail="Stock was not found.")
    if len(ticker) > LONGEST_TICKER or TICKER_PATTERN.fullmatch(ticker) is None:
        raise not_found
    query = select(Company.__table__).where(
        Company.ticker == ticker.upper(), Company.cik.is_not(None)
    )
    company = connection.execute(query).mappings().first()
    if company is None:
        raise not_found
    return company


@router.get("", response_model=list[Stock])
def get_stocks(connection: DatabaseConnection):
    query = (
        select(Company.__table__)
        .where(Company.cik.is_not(None))
        .order_by(Company.ticker)
    )
    return connection.execute(query).mappings().all()


@router.get("/{ticker}", response_model=Stock)
def get_stock(ticker: str, connection: DatabaseConnection):
    return find_company(connection, ticker)


@router.get("/{ticker}/prices", response_model=list[Price])
def get_prices(
    ticker: str,
    connection: DatabaseConnection,
    start_date: date | None = None,
    end_date: date | None = None,
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, detail="start_date must not be later than end_date.")
    company = find_company(connection, ticker)
    query = select(DailyPrice.__table__).where(DailyPrice.company_id == company["id"])
    if start_date:
        query = query.where(DailyPrice.date >= start_date)
    if end_date:
        query = query.where(DailyPrice.date <= end_date)
    return connection.execute(query.order_by(DailyPrice.date)).mappings().all()


@router.get("/{ticker}/financials/annual", response_model=list[AnnualPeriod])
def get_annual_financials(
    ticker: str, connection: DatabaseConnection, as_of: date | None = None
):
    """Latest values by default; with as_of, the values known on that filing date.

    Growth comes from one annual report filed by the same date, never from the series.
    """
    if as_of is not None and as_of > date.today():
        raise HTTPException(422, detail="as_of must not be a future date.")
    company = find_company(connection, ticker)
    facts = connection.execute(reviewed_facts_query(company["id"])).mappings().all()
    if as_of is None:
        periods = normalize_eps(company["ticker"], annual_periods(facts))
        add_table_growth(periods, facts, fiscal_rules())
        return periods

    versions_query = select(AnnualMetricVersion.__table__).where(
        AnnualMetricVersion.company_id == company["id"],
        AnnualMetricVersion.metric.in_(METRIC_NAMES),
    )
    stored_versions = connection.execute(versions_query).mappings().all()
    history_missing = HTTPException(
        503, detail="Point-in-time history has not been built."
    )
    if facts and not stored_versions:
        # An empty history would silently look like "nothing was reported yet".
        raise history_missing
    try:
        periods = periods_as_of(facts, stored_versions, as_of)
    except HistoryOutOfDate as error:
        raise history_missing from error
    known_facts = [fact for fact in facts if fact["filed"] <= as_of]
    add_table_growth(periods, known_facts, fiscal_rules())
    return periods
