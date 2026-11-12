"""Select measures taken from SEC facts by fixed rules, without a review of each value.

Each cell (company, annual period, metric) gets a version whenever its selected value
changes. Tags are tried in the listed order, so a backup tag counts only while the main
one is unknown; capex has its own order in select_capex. These cells join the annual
periods of the reviewed metrics and never create a period of their own.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from app.annual_financials import ANNUAL_REPORT_FORMS, QUARTERLY_REPORT_FORMS
from app.eps_splits import share_count_ratio
from app.fiscal_periods import canonical_period
from app.metric_history import MetricVersion
from app.policy_files import load_policy
from app.schemas.stock import AnnualMetric, EpsShareBasis, EpsSplit, FilingValue

SCALE_CORRECTIONS_PATH = (
    Path(__file__).resolve().parents[2] / "data/share-scale-corrections.json"
)
OPERATING_INCOME_EXCLUSIONS_PATH = (
    Path(__file__).resolve().parents[2] / "data/operating-income-exclusions.json"
)
VALUE_CORRECTIONS_PATH = (
    Path(__file__).resolve().parents[2] / "data/automatic-value-corrections.json"
)
CONFIRMED_AUTOMATIC = "automatic"
# Capex is the purchase of property, plant and equipment, then a wider productive-assets
# line. The main tags hold the whole line.
MAIN_CAPEX_TAGS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
)
# An "other" tag holds the whole line only when the report shows no separate line for a
# kind of asset: LLY and ADP tag their only purchase line this way, and VZ and ROP moved
# their capex line to the second tag in 2019 (N121).
OTHER_PPE_TAG = "PaymentsToAcquireOtherPropertyPlantAndEquipment"
OTHER_CAPEX_TAGS = (OTHER_PPE_TAG, "PaymentsToAcquireOtherProductiveAssets")
# Oil and gas producers can show their capex in two lines without a total row: EOG has
# additions to oil and gas properties and additions to other property and equipment.
OIL_AND_GAS_ADDITIONS_TAG = "PaymentsToAcquireOilAndGasPropertyAndEquipment"
# Purchases of a kind of asset that a report can show on its own line. Next to one of
# them an "other" line is only the rest: 5–16% of EOG's capex, and 1–23 million at FANG,
# whose drilling line is in billions. These tags are read, never selected on their own.
SEPARATE_ASSET_PURCHASE_TAGS = (
    OIL_AND_GAS_ADDITIONS_TAG,
    "PaymentsToAcquireOilAndGasProperty",
    "PaymentsToAcquireOilAndGasEquipment",
    "PaymentsToExploreAndDevelopOilAndGasProperties",
    "PaymentsForConstructionInProcess",
    "PaymentsToAcquireEquipmentOnLease",
    "PaymentsToAcquireRealEstate",
)
# The tags imported for each metric; all but capex are selected in this order.
AUTOMATIC_METRICS = {
    "shares_diluted": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ),
    "assets": ("Assets",),
    "capex": (
        *MAIN_CAPEX_TAGS,
        *OTHER_CAPEX_TAGS,
        *SEPARATE_ASSET_PURCHASE_TAGS,
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "preferred_value": (
        "PreferredStockValue",
        "PreferredStockValueOutstanding",
        "PreferredStockIncludingAdditionalPaidInCapital",
        "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
        "PreferredStockLiquidationPreferenceValue",
    ),
    "preferred_dividends": (
        "PreferredStockDividendsIncomeStatementImpact",
        "DividendsPreferredStock",
        "DividendsPreferredStockCash",
        "PaymentsOfDividendsPreferredStockAndPreferenceStock",
        "PreferredStockDividendsAndOtherAdjustments",
        "DividendsPreferredStockStock",
        "ConvertiblePreferredDividendsNetOfTax",
    ),
}
BALANCE_METRICS = ("assets", "preferred_value")
SHARE_METRIC = "shares_diluted"
BASIC_SHARES_TAG = "WeightedAverageNumberOfSharesOutstandingBasic"
# Annual dividends above this share of a preferred value mean the value is only par
# value or a scale error: GE shows 6 million against a 5795 million preference. Stated
# dividend rates in the catalogue stay below 10%.
LARGEST_PREFERRED_DIVIDEND_RATE = Decimal("0.25")
# A count under this share of the company's largest count is a scale error of the tag,
# compared after splits: MCD tags 732.3 million as 732.3 shares, and its report and those
# of DLR, COP, PCAR, COR and AMT show the true number. No real count falls that far.
SMALLEST_SHARE_COUNT_RATIO = Decimal("0.01")
# Dividends up to this share of equity mean preferred stock below the 5% threshold,
# assuming a dividend rate of at least 4%. AXP's 3.55% shows it is an assumption.
IMMATERIAL_DIVIDENDS_OF_EQUITY = Decimal("0.002")
# The correction rule for data added from 03.11.2026 (project-principles.md, 5.1).
TOLERATED_SHARE_OF_VALUE = Decimal("0.05")
TOLERATED_SHARE_OF_REVENUE = Decimal("0.005")
LARGEST_REVENUE_PART_OF_VALUE = Decimal("0.25")


def share_scale_corrections():
    """Share counts whose unit is confirmed by the table of their own report."""
    return load_policy(SCALE_CORRECTIONS_PATH, "share-scale")["corrections"]


def operating_income_exclusions():
    """Companies whose operating income tag is not a company-wide figure."""
    policy = load_policy(OPERATING_INCOME_EXCLUSIONS_PATH, "operating-income")
    return policy["exclusions"]


def value_corrections():
    """Cells whose tag is wrong above the thresholds, with the value their report states."""
    return load_policy(VALUE_CORRECTIONS_PATH, "value-corrections")["corrections"]


def without_excluded_operating_income(facts, ticker, exclusions):
    """Leave these cells empty; a rule matching nothing means its evidence is gone."""
    result = list(facts)
    for exclusion in exclusions:
        if exclusion["ticker"] != ticker:
            continue
        last_end = exclusion.get("period_end_to")
        kept = [
            fact
            for fact in result
            if fact["metric"] != "operating_income"
            or (last_end is not None and str(fact["period_end"]) > last_end)
        ]
        if len(kept) == len(result):
            raise ValueError(f"Operating income exclusion matches no fact: {exclusion}")
        result = kept
    return result


def matches_correction(fact, correction):
    return (
        fact["metric"] == SHARE_METRIC
        and fact["period_kind"] == "annual"
        and fact["accession"] == correction["accession"]
        and fact["tag"] == correction["tag"]
        and str(fact["period_end"]) == correction["period_end"]
    )


def with_scale_corrections(facts, ticker, corrections):
    """Give listed facts the unit of their report, keeping what the tag says.

    The value must still equal the reviewed one, so a changed source stops the build
    instead of silently multiplying a different number.
    """
    result = list(facts)
    for correction in corrections:
        if correction["ticker"] != ticker:
            continue
        matches = [
            index
            for index, fact in enumerate(result)
            if matches_correction(fact, correction)
        ]
        if not matches:
            raise ValueError(f"Share scale correction matches no fact: {correction}")
        for index in matches:
            fact = result[index]
            tag_value = fact["value"]
            if tag_value != Decimal(correction["reported_value"]):
                raise ValueError(f"Share scale correction is stale: {correction}")
            result[index] = {
                **fact,
                "value": tag_value * correction["multiplier"],
                "tag_value": tag_value,
                "scale_multiplier": correction["multiplier"],
            }
    return result


def latest_of_tag(facts, tag):
    """The newest report of one tag; on one filing date, the highest accession."""
    reported = [fact for fact in facts if fact["tag"] == tag]
    if not reported:
        return None
    return max(reported, key=lambda fact: (fact["filed"], fact["accession"]))


def select_by_tag_order(facts, metric):
    for tag in AUTOMATIC_METRICS[metric]:
        selected = latest_of_tag(facts, tag)
        if selected is not None:
            return selected
    return None


def same_report_fact(facts, reported, tag):
    """The fact of this tag in the report of another fact, or None."""
    for fact in facts:
        if fact["accession"] == reported["accession"] and fact["tag"] == tag:
            return fact
    return None


def has_separate_asset_line(facts, reported):
    """Whether the report of a fact shows a purchase line for a kind of asset."""
    for fact in facts:
        same_report = fact["accession"] == reported["accession"]
        if same_report and fact["tag"] in SEPARATE_ASSET_PURCHASE_TAGS:
            return True
    return False


def select_capex(facts):
    """The facts that add up to capex in the newest report stating it, or an empty list.

    A main tag counts first. Then the two lines of an oil and gas producer, which must
    both come from one report. Then an "other" tag, if its report shows no separate line
    for a kind of asset; otherwise the cell stays empty rather than holding a part.
    """
    for tag in MAIN_CAPEX_TAGS:
        selected = latest_of_tag(facts, tag)
        if selected is not None:
            return [selected]

    oil_and_gas = latest_of_tag(facts, OIL_AND_GAS_ADDITIONS_TAG)
    if oil_and_gas is not None:
        other = same_report_fact(facts, oil_and_gas, OTHER_PPE_TAG)
        if other is None:
            return []
        return [oil_and_gas, other]

    for tag in OTHER_CAPEX_TAGS:
        selected = latest_of_tag(facts, tag)
        if selected is None:
            continue
        if has_separate_asset_line(facts, selected):
            continue
        return [selected]
    return []


def select_preferred_value(facts, dividends):
    """The first plausible positive value; a reported zero only if there is none."""
    reported_zero = None
    for tag in AUTOMATIC_METRICS["preferred_value"]:
        selected = latest_of_tag(facts, tag)
        if selected is None:
            continue
        value = selected["value"]
        if value == 0:
            reported_zero = reported_zero or selected
            continue
        if value < 0:
            continue
        if dividends is None or dividends <= value * LARGEST_PREFERRED_DIVIDEND_RATE:
            return selected
    return reported_zero


def preferred_deduction(value, dividends, equity):
    """What P/B and ROE of common shareholders subtract from equity; None leaves them empty.

    A positive value was already tested against dividends when its cell was selected.
    A missing or zero value proves nothing while the company pays preferred dividends.
    """
    if value is not None and value > 0:
        return value
    if dividends is None or dividends <= 0:
        return Decimal(0)
    if equity is not None and equity > 0:
        if dividends <= equity * IMMATERIAL_DIVIDENDS_OF_EQUITY:
            return Decimal(0)
    return None


def largest_tolerated_difference(metric, value, revenue):
    """A larger difference from a reliable number is an error worth correcting."""
    share_of_value = abs(value) * TOLERATED_SHARE_OF_VALUE
    if metric == SHARE_METRIC or revenue is None:
        return share_of_value
    revenue_part = min(
        abs(revenue) * TOLERATED_SHARE_OF_REVENUE,
        abs(value) * LARGEST_REVENUE_PART_OF_VALUE,
    )
    return max(share_of_value, revenue_part)


def normalized_shares(fact, ticker, split_policy):
    """Put a share count on the current basis, as EPS: splits after the filing apply."""
    pending = [
        EpsSplit.model_validate(action)
        for action in split_policy["actions"]
        if action["ticker"] == ticker
        and fact["filed"] < date.fromisoformat(action["date"])
    ]
    ratio = share_count_ratio(pending)
    if ratio == 1:
        return fact["value"], None
    with localcontext() as context:
        context.prec = 60
        value = fact["value"] * Decimal(ratio.numerator) / Decimal(ratio.denominator)
        value = value.quantize(Decimal("0.0000000001"))
    basis = EpsShareBasis(
        as_of=split_policy["basis_date"],
        reported_value=fact["value"],
        new_shares=ratio.numerator,
        old_shares=ratio.denominator,
        applied_splits=pending,
    )
    return value, basis


def without_share_scale_errors(facts, ticker, split_policy):
    """Drop annual share counts in a wrong scale; other reports or tags fill the cell."""
    counts = {}
    for fact in facts:
        if fact["metric"] == SHARE_METRIC and fact["period_kind"] == "annual":
            counts[id(fact)] = normalized_shares(fact, ticker, split_policy)[0]
    if not counts:
        return facts
    smallest_allowed = max(counts.values()) * SMALLEST_SHARE_COUNT_RATIO
    return [
        fact
        for fact in facts
        if id(fact) not in counts or counts[id(fact)] >= smallest_allowed
    ]


def source_of(fact):
    return FilingValue(
        source_cik=fact["source_cik"],
        taxonomy=fact["taxonomy"],
        tag=fact["tag"],
        unit=fact["unit"],
        # A source shows what the filing says, also when its unit was corrected.
        value=fact.get("tag_value", fact["value"]),
        period_start=fact["period_start"],
        period_end=fact["period_end"],
        filed=fact["filed"],
        accession=fact["accession"],
        form=fact["form"],
    )


def cell_metric(metric, fact, ticker, split_policy):
    """The cell's content in the form the stored history keeps for every metric."""
    value = fact["value"]
    basis = None
    calculation = None
    if metric == SHARE_METRIC:
        value, basis = normalized_shares(fact, ticker, split_policy)
        if basis is not None:
            calculation = f"Reported shares × {basis.new_shares} / {basis.old_shares}"
    note = "Selected automatically, not reviewed value by value."
    if "scale_multiplier" in fact:
        unit_step = f"Tag value × {fact['scale_multiplier']}"
        calculation = f"{unit_step}; {calculation}" if calculation else unit_step
        note += " The tag lacks the unit that the report's own table states."
    if fact["tag"] == BASIC_SHARES_TAG:
        note += " Basic share count: the company reports no diluted count."
    return AnnualMetric(
        status="ready",
        value=value,
        variants=[],
        selected_tag=fact["tag"],
        selected_sources=[source_of(fact)],
        calculation=calculation,
        selection_note=note,
        eps_share_basis=basis,
    )


def summed_cell_metric(parts):
    """A cell added up from lines of one report that shows no total row."""
    with localcontext() as context:
        context.prec = 50
        value = sum((part["value"] for part in parts), Decimal(0))
    return AnnualMetric(
        status="ready",
        value=value,
        variants=[],
        selected_tag=None,
        selected_sources=[source_of(part) for part in parts],
        calculation=" + ".join(part["tag"] for part in parts),
        selection_note=(
            "Selected automatically, not reviewed value by value. Added up from lines "
            "of one report, which shows no total."
        ),
    )


def facts_by_cell(facts, periods, period_rules):
    """Group usable facts by (metric, start, end); other periods are left out."""
    periods_by_end = defaultdict(list)
    for start, end in periods:
        periods_by_end[end].append((start, end))
    supported_forms = ANNUAL_REPORT_FORMS + QUARTERLY_REPORT_FORMS
    cells = defaultdict(list)
    for fact in facts:
        if not fact.get("history_eligible", True):
            continue
        if fact["form"] not in supported_forms:
            continue
        is_balance = fact["metric"] in BALANCE_METRICS
        if fact["period_kind"] != ("instant" if is_balance else "annual"):
            continue
        start, end = canonical_period(fact, period_rules)
        if is_balance:
            matching = periods_by_end[end]
        else:
            matching = [(start, end)] if (start, end) in periods else []
        for period in matching:
            cells[period].append(fact)
    return cells


def corrected_version(correction, period):
    """The one version of a listed cell; its components must add up to the value."""
    start, end = period
    value = Decimal(correction["value"])
    known_from = date.fromisoformat(correction["known_from"])
    sources = [
        FilingValue(
            source_cik=correction["source_cik"],
            taxonomy=source["taxonomy"],
            tag=source["tag"],
            unit=correction["unit"],
            value=Decimal(source["value"]),
            period_start=end if correction["metric"] in BALANCE_METRICS else start,
            period_end=end,
            filed=known_from,
            accession=correction["accession"],
            form=correction["form"],
        )
        for source in correction["sources"]
    ]
    if sum(source.value for source in sources) != value:
        raise ValueError(f"Value correction does not add up: {correction}")
    is_calculated = len(sources) > 1
    metric = AnnualMetric(
        status="ready",
        value=value,
        variants=[],
        selected_tag=None if is_calculated else sources[0].tag,
        selected_sources=sources,
        calculation=(
            " + ".join(source.tag for source in sources) if is_calculated else None
        ),
        selection_note=(
            "Taken from the report, because the tag holds another number "
            f"({correction['finding']})."
        ),
    )
    return MetricVersion(
        period_start=start,
        period_end=end,
        metric=correction["metric"],
        known_from=known_from,
        accession=correction["accession"],
        form=correction["form"],
        confirmation=CONFIRMED_AUTOMATIC,
        value=metric,
    )


def with_value_corrections(versions, ticker, periods, corrections):
    """Replace every version of a listed cell; a rule without its period stops the build."""
    result = list(versions)
    for correction in corrections:
        if correction["ticker"] != ticker:
            continue
        matching = [
            period for period in periods if str(period[1]) == correction["period_end"]
        ]
        if len(matching) != 1:
            raise ValueError(f"Value correction matches no period: {correction}")
        period = matching[0]
        result = [
            version
            for version in result
            if version.metric != correction["metric"]
            or (version.period_start, version.period_end) != period
        ]
        result.append(corrected_version(correction, period))
    return result


def automatic_versions(
    ticker,
    facts,
    periods,
    split_policy,
    period_rules,
    scale_corrections=(),
    income_exclusions=(),
    value_corrections=(),
):
    """Build every automatic cell history of one company for its reviewed periods."""
    versions = []
    periods = set(periods)
    facts = without_excluded_operating_income(facts, ticker, income_exclusions)
    facts = with_scale_corrections(facts, ticker, scale_corrections)
    facts = without_share_scale_errors(facts, ticker, split_policy)
    for (start, end), period_facts in facts_by_cell(
        facts, periods, period_rules
    ).items():
        for metric in AUTOMATIC_METRICS:
            cell_facts = [fact for fact in period_facts if fact["metric"] == metric]
            dividend_facts = [
                fact for fact in period_facts if fact["metric"] == "preferred_dividends"
            ]
            # The value test depends on dividends, which may arrive in a later report.
            report_dates = {fact["filed"] for fact in cell_facts}
            if metric == "preferred_value":
                report_dates |= {fact["filed"] for fact in dividend_facts}
            current = None
            current_form = None
            for report_date in sorted(report_dates):
                known = [fact for fact in cell_facts if fact["filed"] <= report_date]
                capex_parts = None
                if metric == "preferred_value":
                    known_dividends = [
                        fact for fact in dividend_facts if fact["filed"] <= report_date
                    ]
                    dividend = select_by_tag_order(
                        known_dividends, "preferred_dividends"
                    )
                    selected = select_preferred_value(
                        known, dividend["value"] if dividend else None
                    )
                elif metric == "capex":
                    capex_parts = select_capex(known)
                    selected = capex_parts[0] if capex_parts else None
                else:
                    selected = select_by_tag_order(known, metric)
                if selected is None:
                    continue
                if capex_parts is not None and len(capex_parts) > 1:
                    cell = summed_cell_metric(capex_parts)
                else:
                    cell = cell_metric(metric, selected, ticker, split_policy)
                # An annual report repeating a value first known from a 10-Q is
                # recorded too: readers that prefer annual reports (W39) must see
                # that the 10-K confirmed it, or they would fall back to an older value.
                annual_confirms_quarterly = (
                    current_form in QUARTERLY_REPORT_FORMS
                    and selected["form"] in ANNUAL_REPORT_FORMS
                )
                repeated = current is not None and current == cell.value
                if repeated and not annual_confirms_quarterly:
                    continue
                current = cell.value
                current_form = selected["form"]
                versions.append(
                    MetricVersion(
                        period_start=start,
                        period_end=end,
                        metric=metric,
                        known_from=report_date,
                        accession=selected["accession"],
                        form=selected["form"],
                        confirmation=CONFIRMED_AUTOMATIC,
                        value=cell,
                    )
                )
    return with_value_corrections(versions, ticker, periods, value_corrections)
