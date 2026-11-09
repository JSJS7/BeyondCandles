"""Check simple relations that the served annual values of any company must satisfy.

The document and identity checks compare a value with its own report. These checks need
no report: they compare served values with each other, e.g. equity cannot exceed assets.
They read every version, not only the latest, and test each day a value was in force,
because a wrong value can live only in the point-in-time history (W29: Linde's assets
for 2017 were 9.2 million for four months).

Two kinds of flags (K12, measured in M36):
- impossible values: equity above assets, negative capital expenditure;
- unusual values that a real event explains: net income or capex above revenue, a share
  count or assets changing several times within a year (mergers, spin-offs).
A flag that a person explained is listed in the reviews, as in the identity checks.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.data_pipeline.identity_checks import separate_reviewed_flags
from app.data_pipeline.source_files import ROOT, read_json, write_json
from app.growth import year_before

REVIEWS = ROOT / "data/invariant-reviews.json"
REPORT = ROOT / "data/processed/invariant-report.json"

CHECKED_METRICS = (
    "revenue",
    "net_income",
    "equity",
    "assets",
    "capex",
    "operating_income",
    "shares_diluted",
)
# Share counts move by a few percent a year; one and a half times means a merger, a
# spin-off or a count in the wrong scale (thousands instead of units).
LARGEST_SHARE_CHANGE = Decimal("1.5")
# Assets triple only through a merger, a deconsolidation or a value of the wrong company.
LARGEST_ASSET_CHANGE = Decimal(3)
DEVIATION_PLACES = Decimal("0.1")


@dataclass(frozen=True)
class Version:
    known_from: date
    status: str
    value: Decimal | None


def percent(ratio):
    """A ratio above 1 as the percent it exceeds 1 by, rounded for reports and reviews."""
    return ((ratio - 1) * 100).quantize(DEVIATION_PLACES)


def in_force(versions, day):
    """The value served on this day, or None before the first version or while unchecked.

    `versions` is sorted by date of knowledge; a later version replaces an earlier one.
    """
    current = None
    for version in versions:
        if version.known_from > day:
            break
        current = version
    if current is None or current.status != "ready":
        return None
    return current.value


def same_period_violations(values):
    """Flags of one period on one day: [(check, deviation in percent)]."""
    flags = []
    equity = values.get("equity")
    assets = values.get("assets")
    if equity is not None and assets is not None and assets > 0 and equity > assets:
        flags.append(("equity_over_assets", percent(equity / assets)))

    capex = values.get("capex")
    if capex is not None and capex < 0:
        # Capital expenditure is a payment; a negative value has a wrong sign or tag.
        flags.append(("negative_capex", Decimal(100)))

    revenue = values.get("revenue")
    if revenue is None or revenue <= 0:
        return flags
    for metric in ("operating_income", "net_income", "capex"):
        amount = values.get(metric)
        if amount is not None and amount > revenue:
            flags.append((f"{metric}_over_revenue", percent(amount / revenue)))
    return flags


def change_ratio(earlier, later):
    """How many times larger the bigger of two positive values is; None otherwise."""
    if earlier is None or later is None or earlier <= 0 or later <= 0:
        return None
    if later >= earlier:
        return later / earlier
    return earlier / later


def year_over_year_violations(earlier, later):
    """Flags of one period against the year before it, on one day."""
    flags = []
    shares_ratio = change_ratio(
        earlier.get("shares_diluted"), later.get("shares_diluted")
    )
    if shares_ratio is not None and shares_ratio > LARGEST_SHARE_CHANGE:
        flags.append(("shares_change", percent(shares_ratio)))
    assets_ratio = change_ratio(earlier.get("assets"), later.get("assets"))
    if assets_ratio is not None and assets_ratio > LARGEST_ASSET_CHANGE:
        flags.append(("assets_change", percent(assets_ratio)))
    return flags


def values_on(cell, day):
    """{metric: value in force on the day} for one period."""
    values = {}
    for metric, versions in cell.items():
        value = in_force(versions, day)
        if value is not None:
            values[metric] = value
    return values


def change_days(*cells):
    """Every day on which a value of these periods changed."""
    days = set()
    for cell in cells:
        for versions in cell.values():
            days.update(version.known_from for version in versions)
    return sorted(days)


def record(flags, ticker, period_end, check, deviation, day):
    """Keep one flag per company, check and period: its largest deviation and first day."""
    key = (ticker, check, period_end)
    flag = flags.get(key)
    if flag is None:
        flags[key] = {
            "ticker": ticker,
            "check": check,
            "year": period_end.year,
            "period_end": str(period_end),
            "first_day": str(day),
            "deviation_percent": deviation,
        }
    elif deviation > flag["deviation_percent"]:
        flag["deviation_percent"] = deviation


def company_flags(ticker, cells, flags):
    """Test every day a value of this company changed; `cells` maps period dates to
    {metric: [Version sorted by date]}."""
    for period, cell in cells.items():
        for day in change_days(cell):
            for check, deviation in same_period_violations(values_on(cell, day)):
                record(flags, ticker, period[1], check, deviation, day)

        earlier_period = year_before(cells, period)
        if earlier_period is None:
            continue
        earlier_cell = cells[earlier_period]
        for day in change_days(cell, earlier_cell):
            earlier = values_on(earlier_cell, day)
            later = values_on(cell, day)
            for check, deviation in year_over_year_violations(earlier, later):
                record(flags, ticker, period[1], check, deviation, day)


def all_flags(cells_by_ticker):
    flags = {}
    for ticker, cells in sorted(cells_by_ticker.items()):
        company_flags(ticker, cells, flags)
    result = []
    for flag in flags.values():
        result.append({**flag, "deviation_percent": str(flag["deviation_percent"])})
    return result


def stored_versions(connection):
    """{ticker: {(start, end): {metric: [Version]}}} from every stored version."""
    from sqlalchemy import bindparam, text

    rows = connection.execute(
        text("""
        SELECT co.ticker, v.period_start, v.period_end, v.metric, v.known_from,
               v.status, v.value
        FROM annual_metric_versions v JOIN companies co ON co.id = v.company_id
        WHERE v.metric IN :metrics
        ORDER BY co.ticker, v.period_start, v.period_end, v.metric, v.known_from, v.id
    """).bindparams(bindparam("metrics", CHECKED_METRICS, expanding=True))
    )
    cells_by_ticker = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in rows:
        period = (row.period_start, row.period_end)
        version = Version(row.known_from, row.status, row.value)
        cells_by_ticker[row.ticker][period][row.metric].append(version)
    return cells_by_ticker


def check_invariants():
    """Run the checks on every stored version; report what no review explains."""
    from app.database import engine

    with engine.connect() as connection:
        cells_by_ticker = stored_versions(connection)
    flags = all_flags(cells_by_ticker)
    document = read_json(REVIEWS)
    if document["version"] != 1:
        raise ValueError("Unsupported invariant reviews version")
    unreviewed, unused = separate_reviewed_flags(flags, document["reviews"])
    counts = defaultdict(int)
    for flag in flags:
        counts[flag["check"]] += 1
    result = {
        "status": "verified" if not unreviewed and not unused else "needs_review",
        "flags_by_check": dict(sorted(counts.items())),
        "flags": len(flags),
        "unreviewed": len(unreviewed),
        "unused_reviews": len(unused),
    }
    write_json(REPORT, {**result, "unreviewed_flags": unreviewed, "unused": unused})
    return result
