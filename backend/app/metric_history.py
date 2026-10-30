"""Build the point-in-time history of every annual metric cell from earlier filings.

A cell (company, annual period, metric) has today's approved value. Each earlier filing
that reported it becomes a version known from that filing date. Rule C: an earlier
version is confirmed when every source of today's value has the identical value in
the same issuer, taxonomy, tag, unit and period. A single-source value that differs
only by coarser rounding, or for EPS only by a later stock split, is also confirmed.
An annual-report version that rule C cannot confirm is confirmed when a review of
that exact filing's statement table (data/version-reviews.json) still matches the
stored facts. Every other annual-report version needs review; an unconfirmed
quarterly (10-Q) version is not recorded, so the previous version stays in force
until the next 10-K.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import partial
from pathlib import Path

from app.annual_financials import (
    ANNUAL_REPORT_FORMS,
    METRIC_NAMES,
    QUARTERLY_REPORT_FORMS,
    annual_periods,
)
from app.eps_splits import (
    apply_share_basis,
    normalize_eps,
    share_count_ratio,
    split_rules,
)
from app.fact_identity import add_selected_sources, fact_identity
from app.fiscal_periods import canonical_period, fiscal_rules, period_metadata
from app.policy_files import load_policy
from app.schemas.stock import AnnualMetric, AnnualPeriod, EpsSplit, FilingValue

VERSION_REVIEWS_PATH = Path(__file__).resolve().parents[2] / "data/version-reviews.json"
CONFIRMED_IDENTICAL = "identical"
CONFIRMED_ROUNDING = "rounding"
CONFIRMED_SPLIT = "split"
CONFIRMED_REVIEWED = "reviewed"
CONFIRMED_APPROVED = "approved"
# Amounts in SEC statements are rounded at most to millions; a coarser apparent
# rounding (e.g. 21 000 000 000) must not widen the tolerance further.
LARGEST_AMOUNT_ROUNDING_DIGITS = 6
# EPS is reported at least to cents, even when trailing zeros were dropped (6.9 = 6.90).
SMALLEST_EPS_DECIMALS = 2


@dataclass
class MetricVersion:
    period_start: date
    period_end: date
    metric: str
    known_from: date
    accession: str
    form: str
    confirmation: str | None
    value: AnnualMetric


@dataclass
class ReportDate:
    """One filing date of a cell, with the report that defines a version on that day."""

    filed: date
    accession: str
    form: str
    annual: bool


def version_reviews():
    """Statement reviews of earlier filings, keyed by review_key."""
    return load_policy(VERSION_REVIEWS_PATH, "version-reviews")["versions"]


def review_key(ticker, metric, start, end, accession):
    return f"{ticker}:{metric}:{start}:{end}:{accession}"


def source_key(source):
    """Identify a reported concept independently of the filing that repeats it."""
    if isinstance(source, FilingValue):
        source = source.model_dump()
    return (
        source["source_cik"],
        source["taxonomy"],
        source["tag"],
        source["unit"],
        source["period_start"],
        source["period_end"],
    )


def half_unit(value, per_share):
    """Half of the last reported digit: the largest error rounding alone can explain."""
    if per_share:
        exponent = value.normalize().as_tuple().exponent
        decimals = max(-exponent, SMALLEST_EPS_DECIMALS)
        return Decimal(5).scaleb(-decimals - 1)
    whole = abs(int(value))
    zeros = 0
    while whole and whole % 10 == 0 and zeros < LARGEST_AMOUNT_ROUNDING_DIGITS:
        whole //= 10
        zeros += 1
    return Decimal(5).scaleb(zeros - 1)


def differs_only_by_rounding(earlier, approved, per_share, earlier_scale=1, scale=1):
    """Check that two rounded values can come from the same exact number.

    Each value stands for an interval of half its last digit; the intervals must
    overlap. The comparison is strict, so values with the same precision must be
    equal: -0.62 and -0.63 are a changed value, not rounding. A scale is the factor
    converting a reported value to the common share basis.
    """
    earlier_value = earlier * earlier_scale
    approved_value = approved * scale
    tolerance = (
        half_unit(earlier, per_share) * earlier_scale
        + half_unit(approved, per_share) * scale
    )
    return abs(earlier_value - approved_value) < tolerance


def split_scale(ticker, filed, *, policy):
    """Factor converting EPS filed on a date to the policy basis (later splits only)."""
    pending = [
        EpsSplit.model_validate(action)
        for action in policy["actions"]
        if action["ticker"] == ticker and filed < date.fromisoformat(action["date"])
    ]
    ratio = share_count_ratio(pending)
    inverse = Fraction(ratio.denominator, ratio.numerator)
    return Decimal(inverse.numerator) / Decimal(inverse.denominator)


def latest_known(facts, filed):
    """The value of one concept known on a date; a same-day conflict is unresolved."""
    known = [fact for fact in facts if fact["filed"] <= filed]
    if not known:
        return None
    latest_date = max(fact["filed"] for fact in known)
    latest = [fact for fact in known if fact["filed"] == latest_date]
    if len({fact["value"] for fact in latest}) != 1:
        return None
    return max(latest, key=lambda fact: fact["accession"])


def cell_report_dates(cell_facts, first_report):
    """Every filing date of the cell from the period's first annual report onwards."""
    by_date = defaultdict(list)
    for fact in cell_facts:
        if fact["filed"] >= first_report.filed:
            by_date[fact["filed"]].append(fact)
    dates = [first_report]
    for filed in sorted(by_date):
        if filed == first_report.filed:
            continue
        facts = by_date[filed]
        annual_facts = [f for f in facts if f["form"] in ANNUAL_REPORT_FORMS]
        chosen = max(annual_facts or facts, key=lambda fact: fact["accession"])
        dates.append(
            ReportDate(
                filed=filed,
                accession=chosen["accession"],
                form=chosen["form"],
                annual=bool(annual_facts),
            )
        )
    return dates


def classify_version(metric, raw, facts_by_key, filed, share_basis):
    """Return (confirmation, sources, reported value) or a review reason for one date.

    share_basis(filed) gives the EPS share-count factor for a filing date, or None
    when the metric is not EPS.
    """
    sources = []
    for component in raw.selected_sources:
        candidates = facts_by_key.get(source_key(component), [])
        # A statement-only position (e.g. noncontrolling interests read from a table)
        # is not a database fact, so rule C cannot confirm its earlier versions.
        if not any(
            fact["accession"] == component.accession
            and fact["value"] == component.value
            for fact in candidates
        ):
            return None, "a source is a statement position outside the facts"
        known = latest_known(candidates, filed)
        if known is None:
            return None, "a source was not yet reported under the approved concept"
        sources.append(known)

    if all(
        known["value"] == component.value
        for known, component in zip(sources, raw.selected_sources, strict=True)
    ):
        return (CONFIRMED_IDENTICAL, sources, raw.value), None
    if len(sources) != 1:
        return None, "a source of a calculated value changed"

    earlier = sources[0]["value"]
    reported = raw.selected_sources[0].value
    per_share = metric == "eps_diluted"
    if per_share and share_basis is not None:
        earlier_scale = share_basis(sources[0]["filed"])
        scale = share_basis(raw.selected_sources[0].filed)
        if earlier_scale != scale and differs_only_by_rounding(
            earlier, reported, per_share, earlier_scale, scale
        ):
            return (CONFIRMED_SPLIT, sources, earlier), None
    if differs_only_by_rounding(earlier, reported, per_share):
        return (CONFIRMED_ROUNDING, sources, earlier), None
    return None, "the reported value differs from the approved value"


def confirmed_metric(raw, sources, value, report, confirmation, eps_context):
    metric = raw.model_copy(deep=True)
    metric.variants = []
    metric.value = value
    metric.selected_sources = [
        FilingValue.model_validate(dict(source)) for source in sources
    ]
    metric.selection_note = (raw.selection_note or "") + (
        f" Point-in-time version from the {report.form} filed {report.filed}"
        f" (confirmed: {confirmation})."
    )
    if eps_context is not None:
        ticker, policy = eps_context
        apply_share_basis(
            metric, ticker, policy, "This is a point-in-time version of the value."
        )
    return metric


def reviewed_metric(review, facts_by_key, report, eps_context):
    """Return the value of a reviewed version, or None when its sources changed.

    Every component must still be a stored fact with the identical value and must
    have been filed by this version's date; a statement position (e.g. a
    noncontrolling interest read from the table) or an EPS fact read from the
    filing's original instance is pinned by the review itself.
    """
    sources = []
    weighted = []
    for component in review["components"]:
        source = FilingValue.model_validate(component["fact"])
        if source.filed > report.filed:
            return None
        # A position read from the statement or the original instance is pinned by
        # the review itself; every other component must be a stored fact.
        pinned = component.get("statement_position") or component.get(
            "instance_position"
        )
        if not pinned:
            stored = facts_by_key.get(source_key(source), [])
            if not any(fact_identity(fact) == component["fact"] for fact in stored):
                return None
        sources.append(source)
        weighted.append(source.value * Decimal(component["coefficient"]))
    with localcontext() as context:
        context.prec = 50
        total = sum(weighted, Decimal(0))
    if total != Decimal(review["value"]):
        return None

    metric = AnnualMetric(
        status="ready",
        value=total,
        variants=[],
        selected_tag=review.get("selected_tag"),
        selected_sources=sources,
        calculation=review.get("calculation"),
        selection_note=(
            f"{review['note']} Point-in-time version from the {report.form} filed "
            f"{report.filed} (confirmed: reviewed against its statement table)."
        ),
        eps_basis=review.get("eps_basis"),
        scope=review.get("scope"),
    )
    if eps_context is not None:
        ticker, policy = eps_context
        apply_share_basis(
            metric, ticker, policy, "This is a point-in-time version of the value."
        )
    return metric


def review_metric(report, reason):
    return AnnualMetric(
        status="needs_review",
        variants=[],
        selection_note=(
            f"The {report.form} filed {report.filed} has not been checked against "
            f"its statement table: {reason}."
        ),
    )


def version_signature(version):
    metric = version.value
    sources = tuple(
        (source.accession, source.value) for source in metric.selected_sources
    )
    return metric.status, version.confirmation, metric.value, sources


def cell_versions(
    metric,
    start,
    end,
    raw,
    final,
    cell_facts,
    facts_by_key,
    first_report,
    eps_context=None,
    reviews=None,
    keep_repeats=False,
):
    """List the versions of one cell, oldest first; the last one equals today's value.

    raw is today's selection before EPS normalization and final the served value.
    eps_context is (ticker, split policy) when today's EPS has a verified share basis.
    reviews maps an accession to the statement review of this cell in that filing.
    keep_repeats lists every report date, also one repeating the previous version.
    """
    reviews = {} if reviews is None else reviews

    def version(report, confirmation, value):
        return MetricVersion(
            period_start=start,
            period_end=end,
            metric=metric,
            known_from=report.filed,
            accession=report.accession,
            form=report.form,
            confirmation=confirmation,
            value=value,
        )

    today = final.model_copy(deep=True)
    today.variants = []
    if final.status != "ready":
        # Without an approved value there is nothing to compare earlier filings with.
        return [version(first_report, None, today)]

    latest_source = max(raw.selected_sources, key=lambda source: source.filed)
    approved_report = ReportDate(
        filed=max(latest_source.filed, first_report.filed),
        accession=latest_source.accession,
        form=latest_source.form,
        annual=latest_source.form in ANNUAL_REPORT_FORMS,
    )
    share_basis = None
    if eps_context is not None:
        ticker, policy = eps_context
        share_basis = partial(split_scale, ticker, policy=policy)

    versions = []
    for report in cell_report_dates(cell_facts, first_report):
        if report.filed >= approved_report.filed:
            break
        decision, reason = classify_version(
            metric, raw, facts_by_key, report.filed, share_basis
        )
        if decision is None:
            if not report.annual:
                # Decision 28.10.2026: an unconfirmed 10-Q version is not recorded.
                continue
            review = reviews.get(report.accession)
            reviewed = None
            if review is not None:
                reviewed = reviewed_metric(review, facts_by_key, report, eps_context)
                reason = "its statement review no longer matches the stored facts"
            if reviewed is None:
                candidate = version(report, None, review_metric(report, reason))
            else:
                candidate = version(report, CONFIRMED_REVIEWED, reviewed)
        else:
            confirmation, sources, value = decision
            metric_value = confirmed_metric(
                raw, sources, value, report, confirmation, eps_context
            )
            candidate = version(report, confirmation, metric_value)
        previous = versions[-1] if versions else None
        repeated = previous and version_signature(previous) == version_signature(
            candidate
        )
        if repeated and not keep_repeats:
            continue
        versions.append(candidate)

    versions.append(version(approved_report, CONFIRMED_APPROVED, today))
    return versions


def first_reports(facts, rules):
    """The first annual report of every period: from that date the period exists."""
    annual_facts = defaultdict(list)
    for fact in facts:
        if fact["form"] in ANNUAL_REPORT_FORMS and fact["period_kind"] == "annual":
            annual_facts[canonical_period(fact, rules)].append(fact)
    first = {}
    for period, period_facts in annual_facts.items():
        first_filed = min(fact["filed"] for fact in period_facts)
        same_day = [fact for fact in period_facts if fact["filed"] == first_filed]
        # Same day: the highest accession, as in the latest-version selection.
        report = max(same_day, key=lambda fact: fact["accession"])
        first[period] = ReportDate(
            first_filed, report["accession"], report["form"], annual=True
        )
    return first


def periods_as_of(facts, stored_versions, as_of, period_rules=None):
    """Assemble the annual table as it was known on a date from stored versions.

    Values, statuses and sources come from the latest version known on that date.
    Variants are those the current selection lists for the facts filed by then
    (each metric annotates them in its own way); fiscal-year labels are today's.
    """
    rules = fiscal_rules() if period_rules is None else period_rules
    eligible = [fact for fact in facts if fact.get("history_eligible", True)]
    known_facts = [fact for fact in facts if fact["filed"] <= as_of]
    variants = {
        (period.period_start, period.period_end): {
            metric: value.variants for metric, value in period.metrics.items()
        }
        for period in annual_periods(known_facts, period_rules=rules)
    }

    current = {}
    for stored in stored_versions:
        if stored["known_from"] > as_of:
            continue
        cell = (stored["period_start"], stored["period_end"], stored["metric"])
        if cell not in current or stored["known_from"] > current[cell]["known_from"]:
            current[cell] = stored

    periods = sorted({(start, end) for start, end, _ in current}, reverse=True)
    result = []
    for start, end in periods:
        metrics = {}
        for metric in METRIC_NAMES:
            stored = current[(start, end, metric)]
            value = AnnualMetric.model_validate(
                {**stored["details"], "variants": variants[(start, end)][metric]}
            )
            # As in the equity and OCF selection: a statement position used by the
            # value is listed with the variants, although it is not a stored fact.
            add_selected_sources(value.variants, value.selected_sources)
            metrics[metric] = value
        fiscal_year, note = period_metadata(eligible, start, end, rules)
        result.append(
            AnnualPeriod(
                fiscal_year=fiscal_year,
                period_note=note,
                period_start=start,
                period_end=end,
                metrics=metrics,
            )
        )
    return result


def company_versions(
    ticker,
    facts,
    split_policy=None,
    period_rules=None,
    reviews=None,
    keep_repeats=False,
):
    """Build every cell history of one company from its stored facts.

    reviews are the version reviews of all companies (review_key → review); an
    empty dict lists the versions as rule C alone leaves them.
    """
    rules = fiscal_rules() if period_rules is None else period_rules
    split_policy = split_rules() if split_policy is None else split_policy
    reviews = version_reviews() if reviews is None else reviews
    eligible = [fact for fact in facts if fact.get("history_eligible", True)]
    raw_periods = annual_periods(facts, period_rules=rules)
    final_periods = normalize_eps(ticker, raw_periods, rules=split_policy)
    reports = first_reports(eligible, rules)

    supported_forms = ANNUAL_REPORT_FORMS + QUARTERLY_REPORT_FORMS
    facts_by_key = defaultdict(list)
    cell_facts = defaultdict(list)
    for fact in eligible:
        if fact["form"] not in supported_forms:
            continue
        if fact["period_kind"] not in ("annual", "instant"):
            continue
        facts_by_key[source_key(fact)].append(fact)
        start, end = canonical_period(fact, rules)
        cell_facts[(fact["metric"], end)].append((start, fact))

    versions = []
    for raw_period, final_period in zip(raw_periods, final_periods, strict=True):
        start = raw_period.period_start
        end = raw_period.period_end
        for metric in METRIC_NAMES:
            raw = raw_period.metrics[metric]
            final = final_period.metrics[metric]
            # Equity is a balance on the period end; other metrics need the full period.
            facts_of_cell = [
                fact
                for fact_start, fact in cell_facts[(metric, end)]
                if metric == "equity" or fact_start == start
            ]
            eps_context = None
            if metric == "eps_diluted" and final.eps_share_basis is not None:
                eps_context = (ticker, split_policy)
            prefix = review_key(ticker, metric, start, end, "")
            cell_reviews = {
                key.removeprefix(prefix): review
                for key, review in reviews.items()
                if key.startswith(prefix)
            }
            versions.extend(
                cell_versions(
                    metric,
                    start,
                    end,
                    raw,
                    final,
                    facts_of_cell,
                    facts_by_key,
                    reports[(start, end)],
                    eps_context,
                    cell_reviews,
                    keep_repeats,
                )
            )
    return versions
