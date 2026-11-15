"""Ratios of one company for every annual period, valued at the fiscal year's last close.

A period's values are those known when its first annual report was filed, including the
year before as that report states it, and its price is the last close on or before the
period end, as data services show historical annual ratios (DEC-41). Price and results
then describe the company in one shape. A later report can restate the year without a
business separated afterwards (IBM 2019 without Kyndryl), while the year-end price still
includes that business, so the latest version would join two different companies.

These are the values the screener showed after that report; the annual financials table
shows the latest versions instead, so a restated year differs between the two.
"""

from sqlalchemy import func, select

from app.annual_financials import (
    ANNUAL_REPORT_FORMS,
    METRIC_NAMES,
    reviewed_facts_query,
)
from app.fiscal_periods import fiscal_rules, period_metadata
from app.models import AnnualMetricVersion
from app.schemas.screener import AnnualRatios
from app.screener import (
    growth_reports,
    known_periods,
    latest_closes,
    load_ratio_rules,
    valued_period,
)


def period_end(period):
    return period[1]


def first_annual_report_days(connection, company_id):
    """Return {(start, end): filing day of the first annual report with a reviewed
    measure of the period}."""
    versions = AnnualMetricVersion.__table__
    query = (
        select(
            versions.c.period_start,
            versions.c.period_end,
            func.min(versions.c.known_from),
        )
        .where(
            versions.c.company_id == company_id,
            versions.c.form.in_(ANNUAL_REPORT_FORMS),
            versions.c.metric.in_(METRIC_NAMES),
        )
        .group_by(versions.c.period_start, versions.c.period_end)
    )
    days = {}
    for start, end, first_day in connection.execute(query):
        days[(start, end)] = first_day
    return days


def fiscal_year_labels(connection, company_id):
    """Return a function giving a period's fiscal year as the annual financials table
    labels it, from the same eligible facts and reviewed rules."""
    facts = [
        fact
        for fact in connection.execute(reviewed_facts_query(company_id)).mappings()
        if fact.get("history_eligible", True)
    ]
    rules = fiscal_rules()

    def fiscal_year(period):
        year, _ = period_metadata(facts, period[0], period[1], rules)
        return year

    return fiscal_year


def annual_ratio_history(connection, company_id, ticker):
    """Return the AnnualRatios of every annual period of the company, newest first.

    A period ending on a day without a session takes the last close before it, at most
    seven days earlier; without one its price ratios stay empty and the others remain.
    """
    rules = load_ratio_rules()
    report_days = first_annual_report_days(connection, company_id)
    fiscal_year = fiscal_year_labels(connection, company_id)

    # Several periods can share a filing day, so each day's versions are read once.
    periods_by_day = {}
    reports_by_day = {}
    for day in set(report_days.values()):
        known = known_periods(connection, day, company_id=company_id)
        periods_by_day[day] = known.get(company_id, {})
        reports_by_day[day] = growth_reports(connection, day, company_id=company_id)

    history = []
    for period in sorted(report_days, key=period_end, reverse=True):
        day = report_days[period]
        closes = latest_closes(connection, period_end(period), company_id=company_id)
        close = closes.get(company_id)
        valued = valued_period(
            ticker,
            company_id,
            period,
            periods_by_day[day],
            close,
            reports_by_day[day],
            rules,
        )
        labelled = AnnualRatios(**valued.model_dump(), fiscal_year=fiscal_year(period))
        history.append(labelled)
    return history
