"""Year-over-year growth from the columns of one report (M7, M9). No database access.

The series of latest values joins figures from different reports, so a restatement looks
like a change in the business. One report states both years in one definition.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import product

from app.annual_financials import ANNUAL_REPORT_FORMS
from app.fiscal_periods import canonical_period
from app.schemas.stock import GrowthReport

# A definition change above this share gets a marker (project-principles.md, 5.1).
LARGEST_UNMARKED_DIFFERENCE = Decimal("0.05")
# Stored amounts have ten decimal places; sources are written with fewer.
LARGEST_ROUNDING_GAP = Decimal("0.01")
# The period about a year earlier, found by its end date (52/53-week years included).
SMALLEST_YEAR_DAYS = 350
LARGEST_YEAR_DAYS = 380
GROWTH_PRECISION = Decimal("0.000001")


@dataclass(frozen=True)
class ReportColumns:
    """One annual report: its filing date and {period end: {tag: value}}."""

    filed: date
    accession: str
    source_cik: str
    columns: dict


@dataclass(frozen=True)
class ReportYears:
    """Both years as the report that growth was read from states them (N91, M9).

    Shown next to the table's own values when the series breaks, so a reader sees the
    restated earlier year instead of only a percentage.
    """

    accession: str
    filed: date
    source_cik: str
    earlier_value: Decimal
    later_value: Decimal


@dataclass(frozen=True)
class Growth:
    """The change in one report and whether the table's series disagrees with it."""

    value: Decimal | None
    basis_break: bool
    # None when no report states both years.
    report: ReportYears | None = None


@dataclass(frozen=True)
class SeriesCell:
    """A value of the table and how its tags build it: {tag: +1 or -1}."""

    period_end: date
    value: Decimal
    coefficients: dict


def source_coefficients(value, sources):
    """How the sources of a cell add up to its value: {tag: +1 or -1}.

    A bank's revenue is net interest income plus other income, while Philip Morris's 2015
    is net revenues minus excise taxes. Each calculated cell shows which it is, because
    only one choice of signs reproduces it. Empty when none does, so growth stays empty.
    Also empty when two sources share a tag (parent equity as group equity minus a
    statement position with the same tag): a report column holds one value per tag.
    """
    tags = [source["tag"] for source in sources]
    if len(set(tags)) < len(tags):
        return {}
    if len(sources) < 2:
        return {tag: 1 for tag in tags}
    amounts = [Decimal(source["value"]) for source in sources]
    for signs in product((1, -1), repeat=len(amounts)):
        total = sum(sign * amount for sign, amount in zip(signs, amounts, strict=True))
        if abs(total - value) < LARGEST_ROUNDING_GAP:
            return dict(zip(tags, signs, strict=True))
    return {}


def annual_report_columns(facts, period_rules):
    """Group facts of one company and metric by annual report, newest report first.

    Facts of predecessor issuers that do not define today's values are left out. Balance
    sheet values (equity) are instants; a report states at least two year ends.
    """
    reports = {}
    for fact in facts:
        if fact["form"] not in ANNUAL_REPORT_FORMS:
            continue
        if fact["period_kind"] not in ("annual", "instant"):
            continue
        if not fact["history_eligible"]:
            continue
        accession = fact["accession"]
        if accession not in reports:
            reports[accession] = ReportColumns(
                fact["filed"], accession, fact["source_cik"], defaultdict(dict)
            )
        _, period_end = canonical_period(fact, period_rules)
        reports[accession].columns[period_end][fact["tag"]] = fact["value"]
    newest_first = sorted(
        reports.values(),
        key=lambda report: (report.filed, report.accession),
        reverse=True,
    )
    return newest_first


def column_value(column, coefficients):
    return sum(sign * column[tag] for tag, sign in coefficients.items())


def values_in_report(report, earlier, later):
    """Both years' values in this report, with the first tags it states for both.

    A company may change a tag between reports (ASC 606), so the later year's tags are
    tried first and then the earlier year's.
    """
    later_column = report.columns.get(later.period_end)
    earlier_column = report.columns.get(earlier.period_end)
    if later_column is None or earlier_column is None:
        return None
    for coefficients in (later.coefficients, earlier.coefficients):
        if not coefficients:
            continue
        tags = set(coefficients)
        if tags <= set(later_column) and tags <= set(earlier_column):
            earlier_value = column_value(earlier_column, coefficients)
            later_value = column_value(later_column, coefficients)
            return earlier_value, later_value
    return None


def differs_from_series(earlier, later, report_earlier, report_later):
    """True when the series implies a change over 5% away from the report's own.

    A value that changes sign between the two sources is further apart than any percentage.
    """
    if (earlier.value > 0) != (report_earlier > 0):
        return True
    if (later.value > 0) != (report_later > 0):
        return True
    if earlier.value <= 0 or later.value <= 0:
        return False
    series_change = later.value / earlier.value
    report_change = report_later / report_earlier
    difference = abs(series_change / report_change - 1)
    return difference > LARGEST_UNMARKED_DIFFERENCE


def one_report_growth(reports, earlier, later):
    """Growth from the newest report that states both years, or empty when none does.

    The series is never a fallback. A change from or into a loss has no meaningful
    percentage, so it stays empty (project-principles.md, 5.2).
    """
    for report in reports:
        found = values_in_report(report, earlier, later)
        if found is None:
            continue
        report_earlier, report_later = found
        basis_break = differs_from_series(earlier, later, report_earlier, report_later)
        report_years = ReportYears(
            report.accession,
            report.filed,
            report.source_cik,
            report_earlier,
            report_later,
        )
        if report_earlier <= 0 or report_later <= 0:
            return Growth(None, basis_break, report_years)
        growth = report_later / report_earlier - 1
        return Growth(growth, basis_break, report_years)
    return Growth(None, False)


def year_before(periods, period):
    """The period that ended about a year earlier; fiscal-year labels are not compared."""
    _, period_end = period
    for candidate in periods:
        days_between = (period_end - candidate[1]).days
        if SMALLEST_YEAR_DAYS <= days_between <= LARGEST_YEAR_DAYS:
            return candidate
    return None


def table_cell(metric, period_end):
    """A confirmed cell of the annual table as growth reads it, otherwise None."""
    if metric.status != "ready" or metric.value is None:
        return None
    sources = [
        {"tag": source.tag, "value": source.value} for source in metric.selected_sources
    ]
    coefficients = source_coefficients(metric.value, sources)
    return SeriesCell(period_end, metric.value, coefficients)


def add_table_growth(periods, facts, period_rules):
    """Set growth, its report and the series break on every cell of the annual table.

    `periods` is the current or as_of table and `facts` the facts that view may use, so
    growth is known only from reports filed by then. The first known year and a cell
    next to an unconfirmed one have no growth, as in the screener.
    """
    facts_by_metric = defaultdict(list)
    for fact in facts:
        facts_by_metric[fact["metric"]].append(fact)
    reports_by_metric = {}
    for metric, metric_facts in facts_by_metric.items():
        reports_by_metric[metric] = annual_report_columns(metric_facts, period_rules)

    by_dates = {(period.period_start, period.period_end): period for period in periods}
    for period in periods:
        earlier_dates = year_before(by_dates, (period.period_start, period.period_end))
        if earlier_dates is None:
            continue
        earlier_period = by_dates[earlier_dates]
        for metric, cell in period.metrics.items():
            earlier_metric = earlier_period.metrics.get(metric)
            if earlier_metric is None:
                continue
            later = table_cell(cell, period.period_end)
            earlier = table_cell(earlier_metric, earlier_period.period_end)
            if later is None or earlier is None:
                continue
            reports = reports_by_metric.get(metric, [])
            growth = one_report_growth(reports, earlier, later)
            if growth.value is not None:
                cell.growth = growth.value.quantize(GROWTH_PRECISION)
            if growth.report is not None:
                cell.growth_report = GrowthReport(
                    accession=growth.report.accession,
                    filed=growth.report.filed,
                    source_cik=growth.report.source_cik,
                    earlier_period_end=earlier_period.period_end,
                    earlier_value=growth.report.earlier_value,
                    later_value=growth.report.later_value,
                )
            cell.series_break = growth.basis_break
