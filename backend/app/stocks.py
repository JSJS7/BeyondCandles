"""Read the published company catalogue, prices and reported annual financials."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.annual_financials import annual_periods
from app.database import get_connection
from app.eps_splits import normalize_eps
from app.models import Company, DailyPrice, FinancialFact
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
def get_annual_financials(ticker: str, connection: DatabaseConnection):
    company = find_company(connection, ticker)
    query = select(FinancialFact.__table__).where(
        FinancialFact.company_id == company["id"]
    )
    facts = connection.execute(query).mappings().all()
    return normalize_eps(company["ticker"], annual_periods(facts))
