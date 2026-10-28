"""Read the published company catalogue, prices and reported annual financials."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.annual_financials import annual_periods
from app.database import get_connection
from app.eps_splits import normalize_eps
from app.metric_history import periods_as_of
from app.models import AnnualMetricVersion, Company, DailyPrice, FinancialFact
from app.schemas.stock import AnnualPeriod, Price, Stock

router = APIRouter(prefix="/stocks", tags=["stocks"])
DatabaseConnection = Annotated[Connection, Depends(get_connection)]


def find_company(connection, ticker):
    query = select(Company.__table__).where(
        Company.ticker == ticker.upper(), Company.cik.is_not(None)
    )
    company = connection.execute(query).mappings().first()
    if company is None:
        raise HTTPException(404, detail=f"Stock '{ticker.upper()}' was not found.")
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
    """Latest values by default; with as_of, the values known on that filing date."""
    if as_of is not None and as_of > date.today():
        raise HTTPException(422, detail="as_of must not be a future date.")
    company = find_company(connection, ticker)
    query = select(FinancialFact.__table__).where(
        FinancialFact.company_id == company["id"]
    )
    facts = connection.execute(query).mappings().all()
    if as_of is None:
        return normalize_eps(company["ticker"], annual_periods(facts))

    versions_query = select(AnnualMetricVersion.__table__).where(
        AnnualMetricVersion.company_id == company["id"]
    )
    stored_versions = connection.execute(versions_query).mappings().all()
    if facts and not stored_versions:
        # An empty history would silently look like "nothing was reported yet".
        raise HTTPException(503, detail="Point-in-time history has not been built.")
    return periods_as_of(facts, stored_versions, as_of)
