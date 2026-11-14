"""Ratios of all companies as known on one valuation date.

Reads stored cell versions directly, so nothing is recalculated from facts at request
time except growth. The selection follows `periods_as_of`: a cell shows its latest version
known on the date, and that version may be empty when it is not confirmed.
"""

from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.annual_financials import ANNUAL_REPORT_FORMS, METRIC_NAMES
from app.automatic_metrics import periods_without_preferred_shares
from app.database import get_connection
from app.eps_splits import split_rules
from app.fiscal_periods import fiscal_rules
from app.growth import (
    GROWTH_ONLY_METRICS,
    LARGEST_YEAR_DAYS,
    Growth,
    SeriesCell,
    annual_report_columns,
    growth_metric,
    one_report_growth,
    source_coefficients,
    year_before,
)
from app.models import AnnualMetricVersion, Company, DailyPrice, FinancialFact
from app.price_share_basis import (
    price_comparable_to_period,
    share_basis_price,
    spin_offs_after_period,
)
from app.ratios import (
    PeriodValues,
    calculate_ratios,
    exchangeable_units,
    has_material_discontinued_operations,
    has_subsidiary_preferred,
    participating_preferred_tickers,
    shares_for_ratios,
    subsidiary_preferred_periods,
    without_subsidiary_preferred,
)
from app.schemas.screener import (
    PeriodRatios,
    Ratios,
    ScreenerResponse,
    ScreenerRow,
    SpinOff,
)
from app.timeline import timeline_rules

router = APIRouter(prefix="/screener", tags=["screener"])
DatabaseConnection = Annotated[Connection, Depends(get_connection)]

# The last date with prices, and the first with an annual report for every company.
LATEST_VALUATION_DATE = date(2025, 12, 31)
EARLIEST_VALUATION_DATE = date(2016, 3, 31)
# Longer than any market closure in the window; an older price is not a price of that day.
PRICE_LOOKBACK_DAYS = 7
FACT_PERIOD_END_MARGIN_DAYS = 31
RATIO_PRECISION = Decimal("0.000001")
GROWTH_METRICS = {"revenue": "revenue_growth", "eps_diluted": "eps_growth"}


def oldest_needed_period_end(connection, day):
    """The oldest period end among every company's latest two years known on the date.

    The whole history of 200 companies takes most of the screener's response time, and
    only these two years are used. None when no report is known yet.
    """
    versions = AnnualMetricVersion.__table__
    query = (
        select(func.max(versions.c.period_end))
        .where(versions.c.known_from <= day, versions.c.metric.in_(METRIC_NAMES))
        .group_by(versions.c.company_id)
    )
    latest_ends = connection.execute(query).scalars().all()
    if not latest_ends:
        return None
    return min(latest_ends) - timedelta(days=LARGEST_YEAR_DAYS)


def known_periods(connection, day, company_id=None, since=None):
    """Return {company id: {(start, end): {metric: version}}} as known on the date.

    A period exists from the first report of a reviewed metric; automatic measures join
    such a period and never create one. `since` leaves out periods ending before it.

    Each cell shows its latest version from an annual report (W39). A quarterly report
    can restate the previous year's balance sheet under a new standard or without a
    separated business months before the next annual report restates the income
    statement, so taking it would join two bases in one ratio (ROE of MSFT FY2017 fell
    from 29.4% to 26.6% that way). A quarterly version is used only while the cell has
    no annual version yet, so a value first known from a 10-Q stays available.
    """
    versions = AnnualMetricVersion.__table__
    query = select(
        versions.c.company_id,
        versions.c.period_start,
        versions.c.period_end,
        versions.c.metric,
        versions.c.known_from,
        versions.c.form,
        versions.c.status,
        versions.c.value,
        versions.c.details,
    ).where(versions.c.known_from <= day)
    if company_id is not None:
        query = query.where(versions.c.company_id == company_id)
    if since is not None:
        query = query.where(versions.c.period_end >= since)
    # Ascending, so a later version known on the date overwrites earlier ones.
    query = query.order_by(versions.c.known_from)

    companies = defaultdict(lambda: defaultdict(dict))
    cells_with_annual_version = set()
    for version in connection.execute(query).mappings():
        period = (version["period_start"], version["period_end"])
        cell = (version["company_id"], period, version["metric"])
        is_annual = version["form"] in ANNUAL_REPORT_FORMS
        if not is_annual and cell in cells_with_annual_version:
            continue
        if is_annual:
            cells_with_annual_version.add(cell)
        companies[version["company_id"]][period][version["metric"]] = version

    result = {}
    for identifier, periods in companies.items():
        result[identifier] = {
            period: cells
            for period, cells in periods.items()
            if any(metric in cells for metric in METRIC_NAMES)
        }
    return result


def cell_value(cells, metric):
    """The value of a confirmed cell; a version needing review stays empty."""
    version = cells.get(metric)
    if version is None or version["status"] != "ready":
        return None
    return version["value"]


def cell_detail(cells, metric, field):
    version = cells.get(metric)
    if version is None or version["status"] != "ready":
        return None
    return version["details"].get(field)


def series_cell(cells, metric, period_end):
    """The cell as the growth calculation reads it, or None when it is not confirmed."""
    value = cell_value(cells, metric)
    if value is None:
        return None
    sources = cells[metric]["details"].get("selected_sources") or []
    return SeriesCell(period_end, value, source_coefficients(value, sources))


def period_values(cells):
    return PeriodValues(
        revenue=cell_value(cells, "revenue"),
        net_income=cell_value(cells, "net_income"),
        eps=cell_value(cells, "eps_diluted"),
        equity=cell_value(cells, "equity"),
        operating_cash_flow=cell_value(cells, "operating_cash_flow"),
        ocf_scope=cell_detail(cells, "operating_cash_flow", "scope"),
        shares=cell_value(cells, "shares_diluted"),
        assets=cell_value(cells, "assets"),
        capex=cell_value(cells, "capex"),
        operating_income=cell_value(cells, "operating_income"),
        preferred_value=cell_value(cells, "preferred_value"),
        preferred_dividends=cell_value(cells, "preferred_dividends"),
        discontinued_income=cell_value(cells, "discontinued_income"),
        shares_outstanding=cell_value(cells, "shares_outstanding"),
    )


def latest_closes(connection, day, company_id=None):
    """Return {company id: (session, close)} for the last session on or before the date."""
    prices = DailyPrice.__table__
    query = select(prices.c.company_id, prices.c.date, prices.c.close).where(
        prices.c.date <= day,
        prices.c.date >= day - timedelta(days=PRICE_LOOKBACK_DAYS),
    )
    if company_id is not None:
        query = query.where(prices.c.company_id == company_id)
    closes = {}
    for row in connection.execute(query.order_by(prices.c.date)).mappings():
        closes[row["company_id"]] = (row["date"], row["close"])
    return closes


def last_known_filing_day(valuation_date, closes):
    """The last filing date whose reports the valued close can already reflect (M41).

    A filing date carries no time of day, and most annual reports are accepted after the
    market closes (1016 of 1609 measured on 10.11.2026). A report therefore counts only
    from the session after its filing date. Every company trades on the same sessions in
    the window, so the latest session among the closes is the session of the date.
    Without any close there is no price to mislead, and reports filed that day count.
    """
    if not closes:
        return valuation_date
    sessions = [session for session, _ in closes.values()]
    market_session = max(sessions)
    return market_session - timedelta(days=1)


def growth_reports(connection, day, company_id=None, since=None):
    """Return {(company id, metric): annual reports, newest first} known on the date."""
    facts = FinancialFact.__table__
    query = select(
        facts.c.company_id,
        facts.c.metric,
        facts.c.tag,
        facts.c.value,
        facts.c.period_start,
        facts.c.period_end,
        facts.c.filed,
        facts.c.accession,
        facts.c.form,
        facts.c.period_kind,
        facts.c.source_cik,
        facts.c.history_eligible,
    ).where(
        facts.c.metric.in_([*GROWTH_METRICS, *GROWTH_ONLY_METRICS]),
        facts.c.filed <= day,
    )
    if company_id is not None:
        query = query.where(facts.c.company_id == company_id)
    if since is not None:
        # A fact's own period end can differ from the canonical one by some days.
        earliest_fact_end = since - timedelta(days=FACT_PERIOD_END_MARGIN_DAYS)
        query = query.where(facts.c.period_end >= earliest_fact_end)
    grouped = defaultdict(list)
    for fact in connection.execute(query.order_by(facts.c.id)).mappings():
        key = (fact["company_id"], growth_metric(fact["metric"]))
        grouped[key].append(fact)
    rules = fiscal_rules()
    timeline = timeline_rules()
    return {
        key: annual_report_columns(metric_facts, rules, timeline)
        for key, metric_facts in grouped.items()
    }


def period_growth(reports, company_id, metric, period, earlier_period, periods):
    """Growth of one metric; empty in the first known year or for an unconfirmed cell."""
    if earlier_period is None:
        return Growth(None, False)
    later = series_cell(periods[period], metric, period[1])
    earlier = series_cell(periods[earlier_period], metric, earlier_period[1])
    if later is None or earlier is None:
        return Growth(None, False)
    return one_report_growth(reports.get((company_id, metric), []), earlier, later)


def rounded(value):
    return None if value is None else value.quantize(RATIO_PRECISION)


def period_spin_offs(ticker, period_end, session, split_policy):
    """Spin-offs that the price of the session excludes and the period includes."""
    events = spin_offs_after_period(ticker, period_end, session, split_policy)
    result = []
    for event_date, fraction in events:
        separated_fraction = Decimal(fraction.numerator) / Decimal(fraction.denominator)
        result.append(
            SpinOff(
                event_date=event_date, separated_fraction=rounded(separated_fraction)
            )
        )
    return result


def parent_period_values(
    periods, period, ticker, subsidiary_preferred, without_preferred_shares
):
    """The period's values, without preferred stock that belongs to subsidiaries or
    that no longer existed at the period end."""
    values = period_values(periods[period])
    if has_subsidiary_preferred(subsidiary_preferred, ticker, period[1]):
        return without_subsidiary_preferred(values)
    if (ticker, period[1]) in without_preferred_shares:
        return replace(values, no_preferred_shares=True)
    return values


def valued_period(
    ticker,
    company_id,
    period,
    periods,
    close,
    reports,
    split_policy,
    participating,
    units_by_period,
    subsidiary_preferred,
    without_preferred_shares,
):
    """Assemble the ratios of one period valued at the given (session, close)."""
    cells = periods[period]
    values = parent_period_values(
        periods, period, ticker, subsidiary_preferred, without_preferred_shares
    )
    earlier_period = year_before(periods, period)
    previous = None
    if earlier_period is not None:
        previous = parent_period_values(
            periods,
            earlier_period,
            ticker,
            subsidiary_preferred,
            without_preferred_shares,
        )

    session, price = None, None
    if close is not None and price_comparable_to_period(ticker, period[1]):
        session = close[0]
        price = share_basis_price(ticker, session, close[1], split_policy)

    # Without a price there are no price ratios for a spin-off to distort.
    spin_offs = []
    if price is not None:
        spin_offs = period_spin_offs(ticker, period[1], session, split_policy)

    units = units_by_period.get((ticker, period[1]))
    ratios = calculate_ratios(values, previous, price, ticker in participating, units)
    growth = {
        metric: period_growth(
            reports, company_id, metric, period, earlier_period, periods
        )
        for metric in GROWTH_METRICS
    }
    for metric, name in GROWTH_METRICS.items():
        ratios[name] = growth[metric].value
    _, shares_basis = shares_for_ratios(values, units)
    previous_discontinued = False
    if previous is not None:
        previous_discontinued = has_material_discontinued_operations(previous)
    return PeriodRatios(
        period_start=period[0],
        period_end=period[1],
        price_date=session,
        price=rounded(price),
        eps_basis=cell_detail(cells, "eps_diluted", "eps_basis"),
        ocf_scope=values.ocf_scope,
        shares_basis=shares_basis,
        revenue_basis_break=growth["revenue"].basis_break,
        eps_basis_break=growth["eps_diluted"].basis_break,
        spin_offs=spin_offs,
        exchangeable_units=units is not None,
        discontinued_operations=has_material_discontinued_operations(values),
        previous_discontinued_operations=previous_discontinued,
        ratios=Ratios(**{name: rounded(value) for name, value in ratios.items()}),
    )


@router.get("", response_model=ScreenerResponse)
def get_screener(
    connection: DatabaseConnection,
    valuation_date: Annotated[date, Query(alias="date")] = LATEST_VALUATION_DATE,
):
    """Every company's latest annual period known before the date's session, at its close."""
    if not EARLIEST_VALUATION_DATE <= valuation_date <= LATEST_VALUATION_DATE:
        raise HTTPException(
            422,
            detail=(
                f"date must be between {EARLIEST_VALUATION_DATE} "
                f"and {LATEST_VALUATION_DATE}."
            ),
        )
    companies = connection.execute(
        select(Company.__table__)
        .where(Company.cik.is_not(None))
        .order_by(Company.ticker)
    ).mappings()
    closes = latest_closes(connection, valuation_date)
    known_through = last_known_filing_day(valuation_date, closes)
    since = oldest_needed_period_end(connection, known_through)
    periods_by_company = known_periods(connection, known_through, since=since)
    reports = growth_reports(connection, known_through, since=since)
    split_policy = split_rules()
    participating = participating_preferred_tickers()
    units_by_period = exchangeable_units(split_policy)
    subsidiary_preferred = subsidiary_preferred_periods()
    without_preferred_shares = periods_without_preferred_shares()

    rows = []
    for company in companies:
        periods = periods_by_company.get(company["id"], {})
        latest = None
        if periods:
            newest = max(periods, key=lambda period: period[1])
            latest = valued_period(
                company["ticker"],
                company["id"],
                newest,
                periods,
                closes.get(company["id"]),
                reports,
                split_policy,
                participating,
                units_by_period,
                subsidiary_preferred,
                without_preferred_shares,
            )
        rows.append(
            ScreenerRow(
                ticker=company["ticker"],
                name=company["name"],
                sector=company["sector"],
                latest=latest,
            )
        )
    return ScreenerResponse(valuation_date=valuation_date, rows=rows)
