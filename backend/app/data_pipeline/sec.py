"""Extract, audit and import reported SEC facts for the frozen company catalogue."""

import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.annual_financials import ANNUAL_REPORT_FORMS
from app.automatic_metrics import (
    AUTOMATIC_METRICS,
    BALANCE_METRICS,
    COVER_SHARE_METRIC,
    SHARE_METRIC,
)
from app.data_pipeline.source_files import ROOT, read_json, sha256, write_json
from app.data_pipeline.validation import number

METRICS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RegulatedAndUnregulatedOperatingRevenue",
        "RevenuesNetOfInterestExpense",
        "RevenuesExcludingInterestAndDividends",
        "SalesRevenueServicesNet",
        "RealEstateRevenueNet",
        "OilAndGasRevenue",
        "FoodAndBeverageRevenue",
        "HealthCareOrganizationPatientServiceRevenueLessProvisionForBadDebts",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapital",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
}
# Additional revenue facts are limited to reviewed issuers, not treated as
# universal revenue synonyms (advisory fees are only a component for many firms).
REVENUE_SOURCE_TAGS = {
    "0000072971": ("InterestIncomeExpenseNet", "NoninterestIncome"),
    "0000036104": ("InterestIncomeExpenseNet", "NoninterestIncome"),
    "0000927628": ("InterestIncomeExpenseNet", "NoninterestIncome"),
    "0000092230": ("InterestIncomeExpenseNet", "NoninterestIncome"),
    "0001404912": ("InvestmentAdvisoryFees",),
    # Nasdaq tags its revenues less transaction-based expenses as GrossProfit (M51).
    "0001120193": ("GrossProfit",),
    # Philip Morris: excise taxes, deducted from FY2015-FY2017 revenues that its filings
    # still showed including them (data_pipeline/revenue.py, NET_OF_EXCISE_FILINGS).
    "0001413329": ("ExciseAndSalesTaxes",),
}
CACHE = ROOT / "data/raw/sec"
# Annual periods ending in this window match the Benjaminpo price window.
WINDOW_START = date(2015, 1, 1)
WINDOW_END = date(2025, 12, 31)
WINDOW_YEARS = range(WINDOW_START.year, WINDOW_END.year + 1)
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}"
ISO4217 = "http://www.xbrl.org/2003/iso4217"


class SecAccessDenied(ValueError):
    """SEC refused the request (HTTP 403); every concurrent download must stop."""


class CacheIntegrityError(ValueError):
    """A cached SEC file is missing from the cache folder or its bytes changed."""


HISTORICAL_IDENTITIES = {
    "XOM": {
        "cik": "0000034088",
        "name": "EXXON MOBIL CORP",
        "evidence": "https://www.sec.gov/Archives/edgar/data/34088/000003408825000010/xom-20241231.htm",
        "note": "2024 10-K identifies XOM. Current submissions tickers are empty after 2026 successor reorganization; keep historical issuer for 2015-2025 (the 2025 10-K, filed 2026-02-18, is under this CIK).",
    }
}


def fetch(url, user_agent, json_response=True):
    for attempt in range(3):
        try:
            time.sleep(0.25)  # At most four requests/second, sequentially.
            with urlopen(
                Request(
                    url,
                    headers={"User-Agent": user_agent, "Accept": "application/json"},
                ),
                timeout=45,
            ) as response:
                body = response.read(100_000_001)
                if len(body) > 100_000_000:
                    raise ValueError("SEC response exceeds the per-file 100 MB guard")
                if json_response:
                    json.loads(body)  # Never cache an HTML error response as JSON.
                return body
        except HTTPError as exc:
            if exc.code == 403:
                raise SecAccessDenied(
                    "SEC HTTP 403; request stopped, no bypass attempted"
                ) from None
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError(
                    f"SEC HTTP {exc.code}; request stopped, no bypass attempted"
                ) from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise ValueError("SEC request failed after three attempts") from None
        time.sleep(2**attempt)
    raise RuntimeError("Unreachable")


def cache_response(url, user_agent, json_response=True):
    body = fetch(url, user_agent, json_response)
    digest = hashlib.sha256(body).hexdigest()
    path = CACHE / (digest + (".json" if json_response else ".htm"))
    if not path.exists() or sha256(path) != digest:
        path.write_bytes(body)
    return {"url": url, "sha256": digest, "file": path.name}


def usd_unit_ids(root, namespaces):
    """IDs of the instance units that are exactly iso4217:USD (not per share)."""
    units = set()
    for unit in root.findall(XBRLI + "unit"):
        measures = unit.findall(XBRLI + "measure")
        if len(unit) != 1 or len(measures) != 1:
            continue
        prefix, _, name = (measures[0].text or "").strip().partition(":")
        if name == "USD" and namespaces.get(prefix) == ISO4217:
            units.add(unit.attrib["id"])
    return units


def period_kind(start, end, instant=False):
    if instant:
        return "instant"
    days = (end - start).days + 1
    if 70 <= days <= 120:
        return "quarter"
    if 150 <= days <= 210:
        return "six_months_ytd"
    if 240 <= days <= 310:
        return "nine_months_ytd"
    if 330 <= days <= 380:
        return "annual"
    return "other_duration"


def extract_facts(body, metrics=None):
    facts = []
    warnings = []
    seen = {}
    taxonomy = body.get("facts", {}).get("us-gaap", {})
    for metric, tags in (METRICS if metrics is None else metrics).items():
        if metric == "revenue":
            tags = tags + REVENUE_SOURCE_TAGS.get(
                str(body.get("cik", "")).zfill(10), ()
            )
        expected_unit = "USD"
        if metric == "eps_diluted":
            expected_unit = "USD/shares"
        elif metric == SHARE_METRIC:
            expected_unit = "shares"
        for tag in tags:
            units = taxonomy.get(tag, {}).get("units", {})
            for unit, items in units.items():
                if unit != expected_unit:
                    warnings.append(f"Skipped non-target unit {tag}: {unit}")
                    continue
                for fact in items:
                    if not str(WINDOW_START) <= fact["end"] <= str(
                        WINDOW_END
                    ) or fact.get("form") not in (
                        "10-K",
                        "10-K/A",
                        "10-Q",
                        "10-Q/A",
                        "20-F",
                        "20-F/A",
                        "40-F",
                        "40-F/A",
                    ):
                        continue
                    end = date.fromisoformat(fact["end"])
                    filed = date.fromisoformat(fact["filed"])
                    instant = metric == "equity" or metric in BALANCE_METRICS
                    if not instant and not fact.get("start"):
                        warnings.append(f"Missing start: {tag} {fact.get('accn')}")
                        continue
                    start = end if instant else date.fromisoformat(fact["start"])
                    if start > end or filed < end:
                        raise ValueError(f"Invalid SEC dates: {tag} {fact['accn']}")
                    value = number(str(fact["val"]), tag)
                    if (
                        abs(value) >= Decimal("1e28")
                        or cast(int, value.as_tuple().exponent) < -10
                    ):
                        raise ValueError(f"SEC value exceeds NUMERIC(38,10): {tag}")
                    row = {
                        "metric": metric,
                        "taxonomy": "us-gaap",
                        "tag": tag,
                        "unit": unit,
                        "value": value,
                        "period_start": start,
                        "period_end": end,
                        "filed": filed,
                        "accession": fact["accn"],
                        "form": fact["form"],
                        "fiscal_period": fact.get("fp"),
                        "period_kind": period_kind(start, end, instant),
                    }
                    key = (tag, unit, start, end, row["accession"])
                    if key in seen:
                        # Frame/fiscal-year labels may differ on identical reported facts.
                        if any(
                            seen[key][k] != row[k] for k in ("value", "filed", "form")
                        ):
                            raise ValueError(
                                f"Ambiguous values inside one filing: {key}"
                            )
                        continue
                    seen[key] = row
                    facts.append(row)
    return facts, sorted(set(warnings))


def filing_years(taxonomy):
    """Return {accession: (start, end)}, the fiscal year each annual report is for.

    A report repeats earlier years too; its own year is the annual period ending last
    before the filing. Later periods are forecasts, such as tax benefits expected in the
    next year (PG's report for FY2019). The facts of that year agree on its start, so
    the most frequent start is taken.
    """
    periods_by_accession = defaultdict(Counter)
    for tag_facts in taxonomy.values():
        for items in tag_facts.get("units", {}).values():
            for fact in items:
                if fact.get("form") not in ANNUAL_REPORT_FORMS:
                    continue
                if not fact.get("start"):
                    continue
                if fact["end"] > fact["filed"]:
                    continue
                start = date.fromisoformat(fact["start"])
                end = date.fromisoformat(fact["end"])
                if period_kind(start, end) == "annual":
                    periods_by_accession[fact["accn"]][(start, end)] += 1
    years = {}
    for accession, counts in periods_by_accession.items():
        last_end = max(end for _, end in counts)
        last_periods = [period for period in counts if period[1] == last_end]
        last_periods.sort(key=lambda period: (-counts[period], period[0]))
        years[accession] = last_periods[0]
    return years


def cover_share_facts(body):
    """Cover share counts of annual reports, each as a fact of the report's fiscal year.

    The cover states the shares outstanding on a day shortly before the filing. A cover
    tagged with several days (ADSK 2020 also tags 31.07.2019) counts with its latest day
    up to the filing. A report with no annual period of its own, such as an amendment
    of the cover alone, gives no fact.
    """
    tag = AUTOMATIC_METRICS[COVER_SHARE_METRIC][0]
    taxonomies = body.get("facts", {})
    cover_tag = taxonomies.get("dei", {}).get(tag, {})
    covers = cover_tag.get("units", {}).get("shares", [])
    latest_by_accession = {}
    for fact in covers:
        if fact.get("form") not in ANNUAL_REPORT_FORMS:
            continue
        if fact["end"] > fact["filed"]:
            continue
        current = latest_by_accession.get(fact["accn"])
        if current is None or fact["end"] > current["end"]:
            latest_by_accession[fact["accn"]] = fact
        elif fact["end"] == current["end"] and fact["val"] != current["val"]:
            raise ValueError(f"Ambiguous cover share counts: {fact['accn']}")
    years = filing_years(taxonomies.get("us-gaap", {}))
    facts = []
    for accession, fact in sorted(latest_by_accession.items()):
        if accession not in years:
            continue
        start, end = years[accession]
        if not WINDOW_START <= end <= WINDOW_END:
            continue
        facts.append(
            {
                "metric": COVER_SHARE_METRIC,
                "taxonomy": "dei",
                "tag": tag,
                "unit": "shares",
                "value": number(str(fact["val"]), tag),
                "period_start": start,
                "period_end": end,
                "filed": date.fromisoformat(fact["filed"]),
                "accession": accession,
                "form": fact["form"],
                "fiscal_period": fact.get("fp"),
                "period_kind": "annual",
            }
        )
    return facts


def audit_sec():
    from .sec_catalogue import identifiers, load_all_sec
    from .sec_history import HISTORY_INDEX, prepare_company_facts

    issuers = identifiers()
    history_index = (
        read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    )
    index, companies = load_all_sec()
    report = {
        "retrieved_at": index["retrieved_at"],
        "status": "catalogue_audit",
        "limitations": [
            "Coverage means reported facts exist, not complete comparable quarterly financial statements.",
            "Alternative revenue/net-income tags are retained separately; no automatic summing or mixing definitions.",
            "Later filings may restate earlier periods. Historical queries must filter filed date and select an explicit filing version.",
            "SEC filing date is not necessarily the earlier earnings announcement date; intraday availability is not modeled.",
            "Annual source-document reconciliation must be completed separately; it is not certified by this automatic audit.",
            "This audit performs no Q4 subtraction, P/E calculation or split normalization; the API normalizes EPS separately with data/eps-split-rules.json.",
        ],
        "companies": {},
    }
    for ticker, (entry, body) in companies:
        _, warnings = extract_facts(body)
        history = issuers.get(ticker, {}).get("history")
        raw_facts = prepare_company_facts(entry, body, history, history_index)
        facts = [fact for fact in raw_facts if fact["history_eligible"]]
        metrics = {}
        for metric in METRICS:
            selected = [fact for fact in facts if fact["metric"] == metric]
            by_period = defaultdict(list)
            by_filing_period = defaultdict(list)
            for fact in selected:
                period = (
                    fact["tag"],
                    fact["unit"],
                    fact["period_start"],
                    fact["period_end"],
                )
                filing_period = (
                    fact["unit"],
                    fact["period_start"],
                    fact["period_end"],
                    fact["accession"],
                )
                by_period[period].append(fact)
                by_filing_period[filing_period].append(fact)

            differing_definitions = 0
            for group in by_filing_period.values():
                reported_tags = {fact["tag"] for fact in group}
                reported_values = {fact["value"] for fact in group}
                if len(reported_tags) > 1 and len(reported_values) > 1:
                    differing_definitions += 1

            changed_periods = 0
            for group in by_period.values():
                if len({fact["value"] for fact in group}) > 1:
                    changed_periods += 1

            reported_years = {fact["period_end"].year for fact in selected}
            metrics[metric] = {
                "facts": len(selected),
                "calendar_end_years": sorted(reported_years),
                "missing_calendar_end_years": sorted(
                    set(WINDOW_YEARS) - reported_years
                ),
                "tags": sorted({fact["tag"] for fact in selected}),
                "period_kinds": dict(Counter(fact["period_kind"] for fact in selected)),
                "periods_with_changed_values": changed_periods,
                "filing_periods_with_differing_definitions": differing_definitions,
            }
            if differing_definitions:
                warnings.append(
                    f"{metric}: alternative tags have different values within {differing_definitions} filing-periods; select an explicit definition before computing ratios"
                )
        if ticker in HISTORICAL_IDENTITIES:
            warnings.append(HISTORICAL_IDENTITIES[ticker]["note"])
        if not body.get("facts", {}).get("us-gaap"):
            availability = "unsupported_taxonomy"
            warnings.append(
                "No US-GAAP facts; other taxonomies are not mapped to USD metrics."
            )
        elif not facts:
            availability = "no_matching_facts"
            warnings.append(
                "No supported facts in the selected period, forms and units."
            )
        else:
            availability = "available"
        report["companies"][ticker] = {
            "availability": availability,
            "cik": entry["cik"],
            "name": entry["name"],
            "source_sha256": entry["facts"]["sha256"],
            "raw_facts": len(raw_facts),
            "history_facts": len(facts),
            "excluded_facts": len(raw_facts) - len(facts),
            "history_note": history["note"] if history else None,
            "source_ciks": sorted({fact["source_cik"] for fact in raw_facts}),
            "metrics": metrics,
            "warnings": warnings,
        }
    write_json(ROOT / "data/processed/sec-catalogue-report.json", report)
    return report


def fact_key(fact):
    """Identify a reported fact independently of its download or numeric value."""
    return (
        fact["source_cik"],
        fact["metric"],
        fact["taxonomy"],
        fact["tag"],
        fact["unit"],
        fact["period_start"],
        fact["period_end"],
        fact["accession"],
    )


def prepare_all_company_facts():
    """Return the facts the cached SEC files and rules define, by company."""
    from app.metric_history import version_reviews

    from .eps import reviewed_eps_records
    from .sec_catalogue import identifiers, load_all_sec
    from .sec_history import (
        HISTORY_INDEX,
        prepare_automatic_facts,
        prepare_company_facts,
        prepare_report_column_facts,
    )

    issuers = identifiers()
    history_index = (
        read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    )
    eps_records = reviewed_eps_records()
    reviews = version_reviews()
    _, companies = load_all_sec()
    prepared = {}
    for ticker, (entry, body) in companies:
        try:
            approved = issuers.get(ticker, {})
            if approved and approved["cik"] != entry["cik"]:
                raise ValueError("Primary CIK differs from approved identifiers")
            history = approved.get("history")
            facts = prepare_company_facts(entry, body, history, history_index)
            facts += prepare_automatic_facts(entry, body, history, history_index)
            facts += prepare_report_column_facts(
                entry, body, history, history_index, eps_records, reviews
            )
        except ValueError as exc:
            raise ValueError(f"{ticker}: {exc}") from exc
        prepared[ticker] = (entry, facts, history)
    return prepared


def fact_differences(expected_facts, stored_rows):
    """Compare one company's stored facts with the expected ones, field by field.

    Returns the identifying keys of facts that are missing, unexpected or changed.
    Unlike an import, this also compares history_eligible, which a reviewed rule change
    may alter: verification must notice when an import was not run afterwards.
    """
    stored_by_key = {fact_key(row): row for row in stored_rows}
    expected_by_key = {fact_key(fact): fact for fact in expected_facts}
    missing = sorted(set(expected_by_key) - set(stored_by_key), key=str)
    unexpected = sorted(set(stored_by_key) - set(expected_by_key), key=str)
    changed = []
    for key, fact in expected_by_key.items():
        stored = stored_by_key.get(key)
        if stored is None:
            continue
        if any(stored[column] != value for column, value in fact.items()):
            changed.append(key)
    return {
        "missing": missing,
        "unexpected": unexpected,
        "changed": sorted(changed, key=str),
    }


def verify_stored_facts(connection):
    """Fail unless the stored SEC facts equal those from the cache; return their count."""
    from sqlalchemy import select

    from app.models import Company, FinancialFact

    prepared = prepare_all_company_facts()
    companies = connection.execute(
        select(Company.id, Company.ticker, Company.cik)
    ).all()
    problems = []
    checked = 0
    for company in companies:
        stored_rows = (
            connection.execute(
                select(FinancialFact.__table__).where(
                    FinancialFact.company_id == company.id
                )
            )
            .mappings()
            .all()
        )
        if company.ticker not in prepared:
            if stored_rows:
                problems.append(f"{company.ticker}: facts outside the SEC catalogue")
            continue
        entry, facts, _ = prepared[company.ticker]
        if company.cik != entry["cik"]:
            problems.append(f"{company.ticker}: stored CIK differs")
        differences = fact_differences(facts, stored_rows)
        checked += len(stored_rows)
        for kind, keys in differences.items():
            if keys:
                problems.append(
                    f"{company.ticker}: {len(keys)} {kind}, first {keys[0]}"
                )
    tickers = {company.ticker for company in companies}
    for ticker in sorted(set(prepared) - tickers):
        problems.append(f"{ticker}: company missing from the database")
    if problems:
        shown = "; ".join(problems[:5])
        raise ValueError(
            f"Stored SEC facts differ from the cached sources in {len(problems)} "
            f"places: {shown}"
        )
    return checked


def import_sec():
    from sqlalchemy import bindparam, select
    from sqlalchemy.dialects.postgresql import insert

    from app.database import engine, lock_data_imports
    from app.models import Company, FinancialFact

    from .metric_history import rebuild_history
    from .sec_history import history_eligible

    prepared = prepare_all_company_facts()
    summary = {}
    with engine.begin() as connection:
        lock_data_imports(connection)
        for ticker, (entry, facts, history) in prepared.items():
            company = (
                connection.execute(
                    select(Company.__table__).where(Company.ticker == ticker)
                )
                .mappings()
                .first()
            )
            if not company:
                raise ValueError(f"Import the price catalogue first: {ticker}")
            if company["cik"] and company["cik"] != entry["cik"]:
                raise ValueError(f"Existing CIK conflict: {ticker}")
            connection.execute(
                Company.__table__.update()
                .where(Company.id == company["id"])
                .values(
                    cik=entry["cik"],
                    financial_history_note=history["note"] if history else None,
                )
            )
            existing = (
                connection.execute(
                    select(FinancialFact.__table__).where(
                        FinancialFact.company_id == company["id"]
                    )
                )
                .mappings()
                .all()
            )
            existing_by_key = {fact_key(fact): fact for fact in existing}
            # Membership may change after a reviewed rule update; source values never change.
            membership_updates = []
            for stored in existing:
                eligible = history_eligible(stored, entry["cik"], history)
                if stored["history_eligible"] != eligible:
                    membership_updates.append(
                        {"fact_id": stored["id"], "eligible": eligible}
                    )
            if membership_updates:
                connection.execute(
                    FinancialFact.__table__.update()
                    .where(FinancialFact.id == bindparam("fact_id"))
                    .values(history_eligible=bindparam("eligible")),
                    membership_updates,
                )
            inserted = 0
            for offset in range(0, len(facts), 500):
                batch = []
                for fact in facts[offset : offset + 500]:
                    stored_fact = existing_by_key.get(fact_key(fact))
                    if stored_fact is not None:
                        if any(
                            stored_fact[column] != value
                            for column, value in fact.items()
                            if column != "history_eligible"
                        ):
                            raise ValueError(f"Conflicting stored SEC fact: {ticker}")
                    else:
                        batch.append(dict(fact, company_id=company["id"]))
                if batch:
                    connection.execute(insert(FinancialFact), batch)
                    inserted += len(batch)
            result = {
                "ticker": ticker,
                "facts": len(facts),
                "inserted": inserted,
                "history_updated": len(membership_updates),
                "status": "completed",
            }
            summary[ticker] = result
        # The derived point-in-time history must never describe other facts.
        history = rebuild_history(connection)
    return {"companies": summary, "history": history}
