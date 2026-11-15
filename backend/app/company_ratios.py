"""Ratios of one company for every annual period, valued at the fiscal year's last close.

A period's values are those known when its first annual report was filed, including the
year before as that report states it, and its price is the last close on or before the
period end, as data services show historical annual ratios (DEC-41). Price and results
then describe the company in one shape. A later report can restate the year without a
business separated afterwards (IBM 2019 without Kyndryl), while the year-end price still
includes that business, so the latest version would join two different companies.

These are the values the screener showed after that report; the annual financials table
shows the latest versions instead, so a restated year differs between the two.

An automatic measure that the first report does not give (a tag outside the reviewed list,
a value tagged only as a later comparative) joins the year from the first later report that
gives it, but only when that report states the year in the same shape: the same revenue,
net income and equity (M54). Otherwise it stays empty, as data from another shape would.
"""

from datetime import date

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

# A later value joins a year only when the later report states these the same (M54).
UNCHANGED_SHAPE_METRICS = ("revenue", "net_income", "equity")
# Never taken from a later report: discontinued operations appear later almost only when a
# year is restated, and a cover count belongs to the report on whose cover it stands.
FIRST_REPORT_ONLY_METRICS = {"discontinued_income", "shares_outstanding"}


def period_end(period):
    return period[1]


def is_confirmed(version):
    return version is not None and version["status"] == "ready"


def same_shape(first_cells, later_cells):
    """Whether a later report states the year with the first report's main values."""
    for metric in UNCHANGED_SHAPE_METRICS:
        first = first_cells.get(metric)
        later = later_cells.get(metric)
        if not is_confirmed(first) or not is_confirmed(later):
            return False
        if first["value"] != later["value"]:
            return False
    return True


def cell_first_days(connection, company_id):
    """Return {(start, end, metric): the day the cell's first version became known}."""
    versions = AnnualMetricVersion.__table__
    query = (
        select(
            versions.c.period_start,
            versions.c.period_end,
            versions.c.metric,
            func.min(versions.c.known_from),
        )
        .where(versions.c.company_id == company_id)
        .group_by(versions.c.period_start, versions.c.period_end, versions.c.metric)
    )
    days = {}
    for start, end, metric, first_day in connection.execute(query):
        days[(start, end, metric)] = first_day
    return days


def with_later_automatic_values(period, cells, latest_cells, first_days, periods_on):
    """The period's cells, with automatic measures that only a later report gives.

    `latest_cells` are the period's cells known today; `periods_on(day)` returns every
    period's cells as known on a day.
    """
    result = dict(cells)
    for metric, latest_version in latest_cells.items():
        is_automatic = metric not in METRIC_NAMES
        if not is_automatic or metric in FIRST_REPORT_ONLY_METRICS:
            continue
        if is_confirmed(cells.get(metric)) or not is_confirmed(latest_version):
            continue
        later_day = first_days[(period[0], period[1], metric)]
        later_cells = periods_on(later_day).get(period, {})
        later_version = later_cells.get(metric)
        if not is_confirmed(later_version):
            continue
        if not same_shape(cells, later_cells):
            continue
        result[metric] = later_version
    return result


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

    # Several periods share filing days, so each day's versions are read once.
    periods_by_day = {}

    def periods_on(day):
        if day not in periods_by_day:
            known = known_periods(connection, day, company_id=company_id)
            periods_by_day[day] = known.get(company_id, {})
        return periods_by_day[day]

    reports_by_day = {}
    for day in set(report_days.values()):
        reports_by_day[day] = growth_reports(connection, day, company_id=company_id)
    latest_periods = periods_on(date.max)
    first_days = cell_first_days(connection, company_id)

    history = []
    for period in sorted(report_days, key=period_end, reverse=True):
        day = report_days[period]
        periods = periods_on(day)
        cells = with_later_automatic_values(
            period,
            periods[period],
            latest_periods.get(period, {}),
            first_days,
            periods_on,
        )
        periods_with_later_values = {**periods, period: cells}
        closes = latest_closes(connection, period_end(period), company_id=company_id)
        close = closes.get(company_id)
        valued = valued_period(
            ticker,
            company_id,
            period,
            periods_with_later_values,
            close,
            reports_by_day[day],
            rules,
        )
        labelled = AnnualRatios(**valued.model_dump(), fiscal_year=fiscal_year(period))
        history.append(labelled)
    return history
