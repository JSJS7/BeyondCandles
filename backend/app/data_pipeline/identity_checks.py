"""Check served values with arithmetic against other numbers of the same filing.

The document check shows that a value stands in the right row. These checks test whether
it agrees with numbers it did not come from:

- EPS x weighted shares = income available to common shareholders;
- operating + investing + financing cash flow + exchange effect = change in cash;
- assets - liabilities = equity + noncontrolling interests + temporary equity;
- EPS x the served share count = served net income - preferred dividends.

Filers tag these other numbers less carefully than the statements and many companies
have items the sums do not cover, so the tolerances catch gross errors only: a wrong row
or scale, not a small misstatement. A flag that a person explained is listed in the
reviews with the largest deviation the explanation covers, and larger ones flag again.
"""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from app.annual_financials import METRIC_NAMES
from app.data_pipeline.source_files import ROOT, read_json, write_json

REVIEWS = ROOT / "data/identity-reviews.json"
REPORT = ROOT / "data/processed/identity-report.json"

# Period dates of the same year differ by up to a few days between reports.
PERIOD_DAYS_TOLERANCE = 10
EPS_RELATIVE_TOLERANCE = Decimal("0.03")
EPS_ABSOLUTE_TOLERANCE = Decimal("20000000")
# EPS is rounded to a cent, so the product is uncertain by half a cent per share.
EPS_ROUNDING_PER_SHARE = Decimal("0.006")
SHARE_SCALES = (Decimal(1), Decimal(1000), Decimal(1000000))
CASH_RELATIVE_TOLERANCE = Decimal("0.02")
CASH_ABSOLUTE_TOLERANCE = Decimal("50000000")
# The threshold of a large error (project-principles.md, 5.1): a planted error of 10%
# in equity is caught in 87% of the checked periods.
BALANCE_RELATIVE_TOLERANCE = Decimal("0.05")
BALANCE_ABSOLUTE_TOLERANCE = Decimal("50000000")
SHARES_RELATIVE_TOLERANCE = Decimal("0.05")

DILUTED_SHARES = (
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
)
INCOME_TO_COMMON = (
    "NetIncomeLossAvailableToCommonStockholdersDiluted",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
)
NET_INCOME = ("NetIncomeLoss",)
PREFERRED_DIVIDENDS = (
    "PreferredStockDividendsAndOtherAdjustments",
    "PreferredStockDividendsIncomeStatementImpact",
    "DividendsPreferredStock",
)
INVESTING = (
    "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
)
FINANCING = (
    "NetCashProvidedByUsedInFinancingActivities",
    "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
)
CHANGE_WITH_EXCHANGE_EFFECT = (
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
    "CashAndCashEquivalentsPeriodIncreaseDecrease",
)
CHANGE_WITHOUT_EXCHANGE_EFFECT = (
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect",
    "CashAndCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect",
)
EXCHANGE_EFFECT = (
    "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
    "EffectOfExchangeRateOnCashAndCashEquivalents",
)
DISCONTINUED_CASH = (
    "CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations",
    "CashProvidedByUsedInInvestingActivitiesDiscontinuedOperations",
    "CashProvidedByUsedInFinancingActivitiesDiscontinuedOperations",
)
DISCONTINUED_CASH_TOTAL = ("NetCashProvidedByUsedInDiscontinuedOperations",)
ASSETS = ("Assets",)
LIABILITIES = ("Liabilities",)
NONCONTROLLING = ("MinorityInterest",)
TEMPORARY_EQUITY = (
    "RedeemableNoncontrollingInterestEquityCarryingAmount",
    "RedeemableNoncontrollingInterestEquityFairValue",
    "TemporaryEquityCarryingAmountAttributableToParent",
    "TemporaryEquityCarryingAmountAttributableToNoncontrollingInterest",
)
# The reviewed metrics and the two automatic measures their checks compare with.
CHECKED_METRICS = (*METRIC_NAMES, "shares_diluted", "preferred_dividends")
TAGS = {
    *DILUTED_SHARES,
    *INCOME_TO_COMMON,
    *NET_INCOME,
    *PREFERRED_DIVIDENDS,
    *INVESTING,
    *FINANCING,
    *CHANGE_WITH_EXCHANGE_EFFECT,
    *CHANGE_WITHOUT_EXCHANGE_EFFECT,
    *EXCHANGE_EFFECT,
    *DISCONTINUED_CASH,
    *DISCONTINUED_CASH_TOTAL,
    *ASSETS,
    *LIABILITIES,
    *NONCONTROLLING,
    *TEMPORARY_EQUITY,
}


def collect_facts(bodies):
    """Keep the tags the checks need, from a company's issuer and its predecessors."""
    facts = defaultdict(list)
    for body in bodies:
        gaap = body.get("facts", {}).get("us-gaap", {})
        for tag in TAGS:
            for items in gaap.get(tag, {}).get("units", {}).values():
                facts[tag].extend(items)
    return facts


def close_dates(first, second):
    delta = date.fromisoformat(first) - date.fromisoformat(second)
    return abs(delta.days) <= PERIOD_DAYS_TOLERANCE


def filing_value(facts, tags, accession, start, end):
    """The first of the tags that this filing reports for the period, or None.

    Pass start=None for a balance, which has no start date. Only one filing is read:
    mixing reports would compare restated numbers with original ones.
    """
    for tag in tags:
        for fact in facts.get(tag, []):
            if fact["accn"] != accession or not close_dates(fact["end"], end):
                continue
            fact_start = fact.get("start")
            if start is None:
                if fact_start is None:
                    return Decimal(str(fact["val"]))
            elif fact_start is not None and close_dates(fact_start, start):
                return Decimal(str(fact["val"]))
    return None


def percent_of(amount, base):
    """Deviation in percent of the base, rounded for reports and reviews."""
    if base == 0:
        return Decimal("100.0")
    return (abs(amount) * 100 / abs(base)).quantize(Decimal("0.1"))


def check_eps(eps, net_income, facts, accession, start, end):
    """Compare EPS x diluted shares with the income a common shareholder receives.

    Returns the deviation in percent of income, 0 when within tolerance, or None when
    the filing lacks the share count.
    """
    shares = filing_value(facts, DILUTED_SHARES, accession, start, end)
    if shares is None:
        return None
    incomes = [net_income]
    filing_income = filing_value(facts, NET_INCOME, accession, start, end)
    if filing_income is not None:
        incomes.append(filing_income)
    for tag in INCOME_TO_COMMON:
        reported = filing_value(facts, (tag,), accession, start, end)
        if reported is not None:
            incomes.append(reported)
    preferred = filing_value(facts, PREFERRED_DIVIDENDS, accession, start, end)
    if preferred is not None:
        incomes.extend(income - preferred for income in list(incomes))
    # Some filers tag the count in the thousands or millions the statement shows.
    smallest_deviation = None
    closest_income = net_income
    for scale in SHARE_SCALES:
        product = eps * shares * scale
        for income in incomes:
            deviation = abs(product - income)
            tolerance = max(
                abs(income) * EPS_RELATIVE_TOLERANCE,
                EPS_ABSOLUTE_TOLERANCE,
                shares * scale * EPS_ROUNDING_PER_SHARE,
            )
            if deviation <= tolerance:
                return Decimal(0)
            if smallest_deviation is None or deviation < smallest_deviation:
                smallest_deviation = deviation
                closest_income = income
    return percent_of(smallest_deviation, closest_income)


def check_cash(operating_cash_flow, facts, accession, start, end):
    """Sum the cash flow statement; return the residual in percent of operating cash flow.

    Returns 0 when within tolerance, or None when the filing lacks a needed row.
    """
    investing = filing_value(facts, INVESTING, accession, start, end)
    financing = filing_value(facts, FINANCING, accession, start, end)
    if investing is None or financing is None:
        return None
    exchange = filing_value(facts, EXCHANGE_EFFECT, accession, start, end) or Decimal(0)
    discontinued = filing_value(facts, DISCONTINUED_CASH_TOTAL, accession, start, end)
    if discontinued is None:
        parts = [
            filing_value(facts, (tag,), accession, start, end)
            for tag in DISCONTINUED_CASH
        ]
        discontinued = sum((part for part in parts if part is not None), Decimal(0))
    # A served total already contains discontinued operating cash; a served continuing
    # value does not. Both forms are tried, so a value of either scope can pass.
    discontinued_operating = filing_value(
        facts, DISCONTINUED_CASH[:1], accession, start, end
    ) or Decimal(0)
    other_discontinued = discontinued - discontinued_operating
    flows = operating_cash_flow + investing + financing
    change = filing_value(facts, CHANGE_WITH_EXCHANGE_EFFECT, accession, start, end)
    if change is None:
        change = filing_value(
            facts, CHANGE_WITHOUT_EXCHANGE_EFFECT, accession, start, end
        )
        if change is None:
            return None
        exchange = Decimal(0)
    candidates = [
        flows + exchange - change,
        flows + exchange + discontinued - change,
        flows + exchange + other_discontinued - change,
    ]
    residual = min(candidates, key=abs)
    tolerance = max(
        abs(operating_cash_flow) * CASH_RELATIVE_TOLERANCE, CASH_ABSOLUTE_TOLERANCE
    )
    if abs(residual) <= tolerance:
        return Decimal(0)
    return percent_of(residual, operating_cash_flow)


def filings_with_balance_totals(facts, end):
    """Filings that report both assets and liabilities at this date."""
    with_liabilities = {
        fact["accn"]
        for fact in facts.get(LIABILITIES[0], [])
        if fact.get("start") is None and close_dates(fact["end"], end)
    }
    with_assets = {
        fact["accn"]
        for fact in facts.get(ASSETS[0], [])
        if fact.get("start") is None and close_dates(fact["end"], end)
    }
    return sorted(with_liabilities & with_assets)


def check_balance(equity, facts, accession, end):
    """Compare assets - liabilities with equity; return the residual in percent of equity.

    A value served from a later filing's equity statement often has no balance sheet
    there, so any filing with both totals may confirm it. Returns 0 when one does, the
    smallest residual otherwise, or None when no filing reports both totals. The value's
    own filing comes alone when it has a balance sheet: another filing's restated
    numbers must not excuse it.
    """
    filings = filings_with_balance_totals(facts, end)
    if not filings:
        return None
    if accession in filings:
        filings = [accession]
    deviations = [balance_deviation(equity, facts, filing, end) for filing in filings]
    return min(deviations)


def balance_deviation(equity, facts, accession, end):
    """Residual of one filing's balance sheet in percent of equity; 0 within tolerance."""
    assets = filing_value(facts, ASSETS, accession, None, end)
    liabilities = filing_value(facts, LIABILITIES, accession, None, end)
    net_assets = assets - liabilities
    noncontrolling = filing_value(facts, NONCONTROLLING, accession, None, end)
    temporary_parts = [
        filing_value(facts, (tag,), accession, None, end) for tag in TEMPORARY_EQUITY
    ]
    # Filers repeat one amount under several tags, so each distinct amount counts once.
    temporary = sum({part for part in temporary_parts if part is not None}, Decimal(0))
    # Every candidate contains the served equity. Comparing with the filing's own group
    # equity tag instead made this sum true whatever was served (W24).
    bases = [equity, equity + (noncontrolling or Decimal(0))]
    candidates = []
    for base in bases:
        candidates.append(net_assets - base)
        candidates.append(net_assets - base - temporary)
    residual = min(candidates, key=abs)
    tolerance = max(
        abs(equity) * BALANCE_RELATIVE_TOLERANCE, BALANCE_ABSOLUTE_TOLERANCE
    )
    if abs(residual) <= tolerance:
        return Decimal(0)
    return percent_of(residual, equity)


def check_shares(eps, shares, net_income, preferred_dividends):
    """Compare EPS x shares with served income; also the only test of served net income.

    Both inputs share one split basis, so the product does not depend on later splits.
    Returns the deviation in percent of income, 0 when within tolerance.
    """
    income = net_income - (preferred_dividends or Decimal(0))
    deviation = abs(eps * shares - income)
    tolerance = max(
        abs(income) * SHARES_RELATIVE_TOLERANCE,
        EPS_ABSOLUTE_TOLERANCE,
        shares * EPS_ROUNDING_PER_SHARE,
    )
    if deviation <= tolerance:
        return Decimal(0)
    return percent_of(deviation, income)


def check_period(period, facts):
    """Run the three checks for one fiscal period; return results by check name."""
    start = period["period_start"]
    end = period["period_end"]
    results = {}
    eps_cell = period["metrics"].get("eps_diluted")
    income_cell = period["metrics"].get("net_income")
    if eps_cell and income_cell and eps_cell["eps_basis"] != "basic":
        results["eps_shares"] = check_eps(
            eps_cell["reported_value"],
            income_cell["value"],
            facts,
            eps_cell["accession"],
            start,
            end,
        )
    shares_cell = period["metrics"].get("shares_diluted")
    if eps_cell and income_cell and shares_cell and eps_cell["eps_basis"] != "basic":
        dividends_cell = period["metrics"].get("preferred_dividends")
        results["shares_income"] = check_shares(
            eps_cell["value"],
            shares_cell["value"],
            income_cell["value"],
            dividends_cell["value"] if dividends_cell else None,
        )
    cash_cell = period["metrics"].get("operating_cash_flow")
    if cash_cell:
        results["cash_flow"] = check_cash(
            cash_cell["value"], facts, cash_cell["accession"], start, end
        )
    equity_cell = period["metrics"].get("equity")
    if equity_cell:
        results["balance_sheet"] = check_balance(
            equity_cell["value"], facts, equity_cell["accession"], end
        )
    return results


def separate_reviewed_flags(flags, reviews):
    """Split flags into explained and not; report explanations nothing needs any more.

    A review names the company, check and years it explains and the largest deviation
    in percent. A larger deviation is a new flag: the explanation did not cover it.
    """
    explained_by = {}
    for review in reviews:
        key = (review["ticker"], review["check"])
        explained_by.setdefault(key, []).append(review)
    unreviewed = []
    used = set()
    for flag in flags:
        reviewed = False
        for review in explained_by.get((flag["ticker"], flag["check"]), []):
            within_years = review["from_year"] <= flag["year"] <= review["to_year"]
            within_size = Decimal(flag["deviation_percent"]) <= Decimal(
                review["max_deviation_percent"]
            )
            if within_years and within_size:
                reviewed = True
                used.add(id(review))
                break
        if not reviewed:
            unreviewed.append(flag)
    unused = [review for review in reviews if id(review) not in used]
    return unreviewed, unused


def served_periods(connection):
    """The latest version of every ready cell, grouped by company and fiscal period."""
    from sqlalchemy import bindparam, text

    rows = (
        connection.execute(
            text("""
            SELECT DISTINCT ON (v.company_id, v.period_start, v.period_end, v.metric)
                   co.ticker, v.metric, v.period_start, v.period_end, v.value,
                   v.accession, v.status, v.details
            FROM annual_metric_versions v JOIN companies co ON co.id = v.company_id
            WHERE v.metric IN :metrics
            ORDER BY v.company_id, v.period_start, v.period_end, v.metric,
                     v.known_from DESC
        """).bindparams(bindparam("metrics", CHECKED_METRICS, expanding=True))
        )
        .mappings()
        .all()
    )
    periods = {}
    for row in rows:
        if row["status"] != "ready":
            continue
        key = (row["ticker"], str(row["period_start"]), str(row["period_end"]))
        period = periods.setdefault(
            key,
            {
                "ticker": row["ticker"],
                "period_start": str(row["period_start"]),
                "period_end": str(row["period_end"]),
                "metrics": {},
            },
        )
        details = row["details"]
        share_basis = details.get("eps_share_basis") or {}
        reported = share_basis.get("reported_value")
        period["metrics"][row["metric"]] = {
            "value": row["value"],
            "accession": row["accession"],
            "eps_basis": details.get("eps_basis"),
            "reported_value": Decimal(reported) if reported else row["value"],
        }
    return periods


def check_identities():
    """Run all checks on the served values; report what no review explains."""
    from app.database import engine

    from .sec_catalogue import identifiers, load_all_sec
    from .sec_history import HISTORY_INDEX, read_predecessor

    with engine.connect() as connection:
        periods = served_periods(connection)
    by_ticker = defaultdict(list)
    for period in periods.values():
        by_ticker[period["ticker"]].append(period)

    issuers = identifiers()
    history_index = read_json(HISTORY_INDEX)
    _, companies = load_all_sec()
    counts = Counter()
    flags = []
    for ticker, (_, body) in companies:
        history = issuers[ticker].get("history") or {}
        predecessors = [
            read_predecessor(source, history_index)
            for source in history.get("predecessors", [])
        ]
        facts = collect_facts([body, *predecessors])
        for period in by_ticker[ticker]:
            for check, deviation in check_period(period, facts).items():
                if deviation is None:
                    counts[f"{check}_not_available"] += 1
                    continue
                counts[f"{check}_checked"] += 1
                if deviation > 0:
                    counts[f"{check}_flagged"] += 1
                    flags.append(
                        {
                            "ticker": ticker,
                            "check": check,
                            "year": int(period["period_end"][:4]),
                            "period_end": period["period_end"],
                            "deviation_percent": str(deviation),
                        }
                    )
    document = read_json(REVIEWS)
    if document["version"] != 1:
        raise ValueError("Unsupported identity reviews version")
    unreviewed, unused = separate_reviewed_flags(flags, document["reviews"])
    result = {
        "status": "verified" if not unreviewed and not unused else "needs_review",
        **dict(sorted(counts.items())),
        "flags": len(flags),
        "unreviewed": len(unreviewed),
        "unused_reviews": len(unused),
    }
    write_json(REPORT, {**result, "unreviewed_flags": unreviewed, "unused": unused})
    return result
