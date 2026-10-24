"""Review total-revenue definitions against SEC-rendered income statements."""

import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event, Lock
from time import monotonic, sleep

from app.data_pipeline.identifiers import cached, write_json
from app.data_pipeline.sec import CACHE
from app.data_pipeline.sec_catalogue import identifiers, load_all_sec, verify_reference
from app.data_pipeline.sec_history import HISTORY_INDEX, prepare_company_facts
from app.data_pipeline.sec_statements import (
    matches_rendered_value,
    normalized_label,
    statement_total,
)
from app.data_pipeline.source_files import ROOT, SELECTION, read_json, sha256
from app.revenue import RULES_PATH, period_key, sum_reported_values

from .revenue_facts import (
    SUPPLEMENT_INDEX,
    cache_instance,
    instance_facts,
)

EVIDENCE_INDEX = CACHE / "revenue-evidence-index.json"
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")


def income_statement_files(summary):
    """Restrict evidence to primary income/operations statements, not revenue notes."""
    reports = []
    combined_reports = []
    for report in ET.fromstring(summary).iter("Report"):
        fields = {child.tag: child.text or "" for child in report}
        name = fields.get("ShortName", "").lower()
        category = fields.get("MenuCategory")
        is_statement = category == "Statements"
        if category in ("Uncategorized", None, ""):
            is_statement = " - Statement - " in fields.get("LongName", "")
        if not is_statement or any(
            term in name
            for term in (
                "parenthetical",
                "quarterly results",
                "quarterly financial data",
            )
        ):
            continue
        if any(term in name for term in ("cash flow", "equity", "balance")):
            continue
        if not any(
            term in name
            for term in (
                "income",
                "operations",
                "earnings",
                "profit",
                "comprehensive loss",
            )
        ):
            continue
        filename = fields.get("HtmlFileName", "")
        if re.fullmatch(r"R\d+\.htm", filename):
            if "comprehensive" in name:
                combined_reports.append(filename)
            else:
                reports.append(filename)
    # A separate comprehensive-income statement usually has no revenue. Use it only
    # when the filer presents a combined statement and no ordinary income statement.
    return reports or combined_reports


def fact_period_key(fact, rules=None):
    """Use reviewed fiscal aliases only for identifying the presentation period."""
    from app.fiscal_periods import canonical_period, fiscal_rules

    calendar = fiscal_rules() if rules is None else rules
    start, end = canonical_period(fact, calendar)
    return period_key(fact["source_cik"], start, end)


def review_jobs(facts):
    """Review the latest revenue filing for every annual-report period, never an older tag."""
    from app.fiscal_periods import fiscal_rules

    calendar = fiscal_rules()
    eligible = [
        fact
        for fact in facts
        if fact["history_eligible"]
        and fact["period_kind"] == "annual"
        and fact["form"] in ANNUAL_FORMS + ("10-Q", "10-Q/A")
    ]
    periods = {
        fact_period_key(fact, calendar)
        for fact in eligible
        if fact["form"] in ANNUAL_FORMS
    }
    latest = {}
    groups = defaultdict(list)
    for fact in eligible:
        source = (fact["source_cik"], fact["accession"])
        groups[source].append(fact)
        period = fact_period_key(fact, calendar)
        if fact["metric"] != "revenue" or period not in periods:
            continue
        order = (fact["filed"], fact["accession"])
        if period not in latest or order > latest[period][0]:
            latest[period] = (order, source)

    # An annual period without revenue still needs an explicit unresolved result.
    # Its annual filing provides the review target, not an invented revenue value.
    for period in periods - latest.keys():
        candidates = [
            fact
            for fact in eligible
            if fact["form"] in ANNUAL_FORMS
            and fact_period_key(fact, calendar) == period
        ]
        anchor = max(candidates, key=lambda fact: (fact["filed"], fact["accession"]))
        source = (anchor["source_cik"], anchor["accession"])
        latest[period] = ((anchor["filed"], anchor["accession"]), source)

    covered = defaultdict(set)
    for period, (_, source) in latest.items():
        covered[source].add(period)
    return [(source, groups[source], covered[source]) for source in sorted(covered)]


def download_evidence(
    issuers,
    history_index,
    evidence,
    extra_jobs=None,
    *,
    statement_selector=None,
    evidence_path=None,
):
    """Overlap network latency with four workers and one shared four-requests/second gate."""
    output_path = EVIDENCE_INDEX if evidence_path is None else evidence_path
    jobs = set(extra_jobs or [])
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        facts = prepare_company_facts(
            entry, body, issuers[ticker].get("history"), history_index
        )
        if extra_jobs is None:
            jobs.update(source for source, _, _ in review_jobs(facts))
    gate = Lock()
    checkpoint = Lock()
    stopped = Event()
    last_request = [0.0]

    def download(url, previous):
        if previous and previous["url"] == url:
            try:
                verify_reference(previous)
                return previous
            except (OSError, ValueError):
                pass
        with gate:
            if stopped.is_set():
                raise ValueError(
                    "SEC statement downloads stopped after SEC denied access"
                )
            sleep(max(0, last_request[0] + 0.25 - monotonic()))
            if stopped.is_set():
                raise ValueError(
                    "SEC statement downloads stopped after SEC denied access"
                )
            last_request[0] = monotonic()
        try:
            return cached(url, json_response=False)
        except ValueError as exc:
            if "HTTP 403" in str(exc):
                stopped.set()
            raise

    def download_filing(source):
        cik, accession = source
        key = cik + ":" + accession
        old = evidence.get(key, {}).copy()
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
        try:
            old["summary"] = download(base + "FilingSummary.xml", old.get("summary"))
            summary = verify_reference(old["summary"]).read_bytes()
            if statement_selector is None:
                files = income_statement_files(summary)
            else:
                files = statement_selector(summary, cik)
            for filename in files:
                old[filename] = download(base + filename, old.get(filename))
        except (ValueError, ET.ParseError) as exc:
            if stopped.is_set():
                raise
            old["download_issue"] = str(exc)
        with checkpoint:
            evidence[key] = old
            # Windows file watchers can briefly hold the published JSON during replacement.
            for attempt in range(4):
                try:
                    write_json(output_path, evidence)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    sleep(0.1 * (attempt + 1))

    print(
        f"SEC statement evidence: {len(jobs)} filings to check (existing cache is reused)",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(download_filing, source) for source in sorted(jobs)]
        try:
            for completed, future in enumerate(as_completed(futures), 1):
                future.result()
                if completed % 25 == 0 or completed == len(jobs):
                    print(
                        f"SEC statement evidence: {completed}/{len(jobs)} filings cached",
                        flush=True,
                    )
        except Exception:
            stopped.set()
            for future in futures:
                future.cancel()
            raise


BANK_COMPONENTS = ("InterestIncomeExpenseNet", "NoninterestIncome")
BANK_CIKS = {
    "0000072971",
    "0000036104",
    "0000927628",
    "0000092230",
}  # WFC, USB, COF, TFC


def verified_bank_total(html, end_date, group, period):
    """Verify both GAAP components on the same face statement and filing version."""
    selected = []
    for concept in BANK_COMPONENTS:
        row = statement_total(html, end_date, concept=concept)
        matches = [
            fact
            for fact in group
            if fact["metric"] == "revenue"
            and fact["tag"] == concept
            and fact["taxonomy"] == "us-gaap"
            and fact["unit"] == "USD"
            and period_key(fact["source_cik"], fact["period_start"], fact["period_end"])
            == period
        ]
        if len(matches) != 1 or not matches_rendered_value(
            row, matches[0]["value"], end_date
        ):
            raise ValueError(
                "Bank revenue components do not match the same annual statement"
            )
        selected.append(matches[0])
    return {
        "taxonomy": "us-gaap",
        "tag": None,
        "label": "GAAP net interest income plus noninterest income",
        "components": list(BANK_COMPONENTS),
    }, {
        "value": sum_reported_values(selected),
        "component_values": {fact["tag"]: str(fact["value"]) for fact in selected},
    }


# Exact disclosures reviewed because the primary face omits the consolidated total
# or a quarterly filing corrects a historical full year only in a revenue note.
REVIEWED_NOTE_TOTALS = {
    "0000075362:2015-01-01:2015-12-31": {
        "accession": "0001564590-18-002675",
        "filename": "R111.htm",
        "tag": "Revenues",
        "label": "Net sales and revenues",
        "value": "19115100000",
    },
    "0000882184:2014-10-01:2015-09-30": {
        "accession": "0000882184-17-000103",
        "filename": "R44.htm",
        "tag": "Revenues",
        "label": "Revenues, Total",
        "value": "10824000000",
    },
    "0000882184:2017-10-01:2018-09-30": {
        "accession": "0000882184-20-000143",
        "filename": "R41.htm",
        "tag": "Revenues",
        "label": "Revenues",
        "value": "16068000000",
    },
    "0001069183:2021-01-01:2021-12-31": {
        "accession": "0001069183-24-000061",
        "filename": "R48.htm",
        "tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "label": "Revenue from contract with customers",
        "value": "865638000",
    },
}


def reviewed_disclosure(period, group, source, old, fetch=False, evidence=None):
    """Verify only pinned consolidated note totals, stopping before any segment axis."""
    reviewed = REVIEWED_NOTE_TOTALS.get(period)
    if not reviewed or source[1] != reviewed["accession"]:
        return None
    filename = reviewed["filename"]
    if fetch:
        if evidence is None:
            raise ValueError(
                "Fetching a reviewed disclosure requires an evidence index"
            )
        base = old["summary"]["url"].rsplit("/", 1)[0] + "/"
        old[filename] = cached(base + filename, old.get(filename), json_response=False)
        evidence[source[0] + ":" + source[1]] = old
        write_json(EVIDENCE_INDEX, evidence)
    if filename not in old:
        return None

    reference = old[filename]
    end = datetime.fromisoformat(period.split(":")[2]).date()
    total = statement_total(
        verify_reference(reference).read_text(encoding="utf-8"),
        end,
        concept=reviewed["tag"],
        stop_at_costs=False,
        stop_at_dimensions=True,
    )
    if total["label"] != reviewed["label"]:
        raise ValueError("Reviewed consolidated note label changed")
    selected = [
        fact
        for fact in group
        if fact["metric"] == "revenue"
        and fact_period_key(fact) == period
        and fact["taxonomy"] == "us-gaap"
        and fact["tag"] == reviewed["tag"]
        and fact["unit"] == "USD"
        and fact["value"] == Decimal(reviewed["value"])
    ]
    if len(selected) != 1 or not matches_rendered_value(
        total, selected[0]["value"], end
    ):
        raise ValueError(
            "Reviewed consolidated note does not match the same-filing annual fact"
        )
    return {
        "taxonomy": "us-gaap",
        "tag": reviewed["tag"],
        "label": reviewed["label"],
        "accession": source[1],
        "filed": str(selected[0]["filed"]),
        "value": str(selected[0]["value"]),
        "evidence": reference,
        "summary": old["summary"],
        "note": (
            "Consolidated annual total verified in the same filing's revenue/segment reconciliation, "
            "before segment breakdowns. Quarterly and segment amounts are not substituted."
        ),
    }


def verify_filing_period(
    ticker, period, group, source, old, supplements, fetch, evidence
):
    """Return a source-backed definition only when this issuer's annual amount matches."""
    cik, accession = source
    end_date = datetime.fromisoformat(period.split(":")[2]).date()
    attempts = []
    try:
        summary = old["summary"]
        from app.data_pipeline.net_income import issuer_income_files

        files = issuer_income_files(verify_reference(summary).read_bytes(), cik)
        matches = []
        for filename in files:
            reference = old[filename]
            html = verify_reference(reference).read_text(encoding="utf-8")
            try:
                if cik in BANK_CIKS:
                    total, selected = verified_bank_total(html, end_date, group, period)
                    matches.append((total, selected, reference))
                    continue
                if cik == "0001120193":
                    total = statement_total(
                        html, end_date, concept="GrossProfit", stop_at_costs=False
                    )
                    if (
                        normalized_label(total["label"])
                        != "revenues less transaction based expenses"
                    ):
                        raise ValueError(
                            "Nasdaq net revenue is not explicitly identified on this statement"
                        )
                else:
                    labels = {
                        "VRTX": ("product revenues net",),
                        "KKR": ("fees and other",),
                        "GE": ("total revenues and other income",),
                        "EOG": ("total",),
                    }
                    total = statement_total(
                        html, end_date, extra_labels=labels.get(ticker, ())
                    )
                if total["taxonomy"] != "us-gaap":
                    if fetch:
                        cache_instance(cik, accession, old)
                        evidence[cik + ":" + accession] = old
                        write_json(EVIDENCE_INDEX, evidence)
                    instance = old.get("instance")
                    if not instance:
                        raise ValueError(
                            "Original custom-tag XBRL instance has not been cached"
                        )
                    metadata = {
                        field: group[0][field]
                        for field in ("filed", "form", "fiscal_period", "accession")
                    }
                    original = instance_facts(
                        verify_reference(instance).read_bytes(),
                        cik,
                        metadata,
                        {(total["taxonomy"], total["tag"])},
                    )
                    group = group + original
            except ValueError as exc:
                if "HTTP 403" in str(exc) or "checksum" in str(exc):
                    raise
                attempts.append({"url": reference["url"], "reason": str(exc)})
                continue
            selected = [
                fact
                for fact in group
                if fact_period_key(fact) == period
                and fact["metric"] == "revenue"
                and fact["taxonomy"] == total["taxonomy"]
                and fact["tag"] == total["tag"]
                and fact["unit"] == "USD"
            ]
            values = {fact["value"] for fact in selected}
            if len(values) == 1 and matches_rendered_value(
                total, selected[0]["value"], end_date
            ):
                matches.append((total, selected[0], reference))
                if total["taxonomy"] != "us-gaap":
                    supplements["filings"][cik + ":" + accession] = {
                        "instance": old["instance"],
                        "concepts": [[total["taxonomy"], total["tag"]]],
                        "filed": str(selected[0]["filed"]),
                        "form": selected[0]["form"],
                        "fiscal_period": selected[0]["fiscal_period"],
                    }
        identities = {
            (total["taxonomy"], total["tag"], selected["value"])
            for total, selected, _ in matches
        }
        if len(identities) != 1:
            attempts.append(
                {
                    "source": cik + ":" + accession,
                    "reason": "No unique same-issuer fact matches this annual column",
                }
            )
            return None, attempts
        total, selected, reference = matches[0]
        rule = {
            **({"components": total["components"]} if total.get("components") else {}),
            "taxonomy": total["taxonomy"],
            "tag": total["tag"],
            "label": total["label"],
            "accession": accession,
            "filed": str(max(fact["filed"] for fact in group)),
            "value": str(selected["value"]),
            **(
                {"component_values": selected["component_values"]}
                if total.get("components")
                else {}
            ),
            "evidence": reference,
            "summary": summary,
        }
        if total["taxonomy"] != "us-gaap":
            rule["original_instance"] = old["instance"]
        if cik == "0001120193":
            rule["note"] = (
                "Nasdaq reported net revenue after transaction-based expenses. The issuer uses "
                "us-gaap:GrossProfit for this explicit revenue position; it is not a general gross-profit fallback."
            )
        return rule, attempts
    except (ValueError, KeyError, ET.ParseError) as exc:
        if "HTTP 403" in str(exc) or "checksum" in str(exc):
            raise
        attempts.append({"source": cik + ":" + accession, "reason": str(exc)})
    return None, attempts


def review_revenue(fetch=False):
    """Approve exact latest filing values; unavailable or ambiguous evidence stays unresolved."""
    issuers = identifiers()
    history_index = (
        read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    )
    evidence = read_json(EVIDENCE_INDEX) if EVIDENCE_INDEX.exists() else {}
    supplements = (
        read_json(SUPPLEMENT_INDEX) if SUPPLEMENT_INDEX.exists() else {"filings": {}}
    )
    if fetch:
        download_evidence(issuers, history_index, evidence)

    policy = {
        "version": 2,
        "selection_sha256": sha256(SELECTION),
        "method": (
            "Verify the latest revenue filing separately for every reviewed annual period. "
            "Match the primary income-statement total, issuer, annual date column, USD scale "
            "and exact source fact within displayed rounding. Pin the accession, filing date "
            "and amount; a tag approval never transfers to another filing automatically. "
            "Four exact note exceptions verify consolidated totals or historical annual corrections."
        ),
        "definition": (
            "Reported consolidated revenue on the income statement's tax/interest basis. "
            "Reviewed banks use GAAP net interest income plus noninterest income from one filing. "
            "Overlapping concepts are never summed; different sectors are not normalized."
        ),
        "periods": {},
        "unresolved": {},
    }
    report = {"status": "reviewed", "companies": {}, "totals": {}}
    totals = Counter()
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        facts = prepare_company_facts(
            entry, body, issuers[ticker].get("history"), history_index
        )
        previous_count = len(policy["periods"])
        issues = []
        for source, group, periods in review_jobs(facts):
            cik, accession = source
            old = evidence.get(cik + ":" + accession, {})
            for period in sorted(periods):
                rule, attempts = verify_filing_period(
                    ticker,
                    period,
                    group,
                    source,
                    old,
                    supplements,
                    fetch,
                    evidence,
                )
                if rule is None:
                    rule = reviewed_disclosure(
                        period, group, source, old, fetch, evidence
                    )
                if rule:
                    policy["periods"][period] = rule
                else:
                    issue = {
                        "ticker": ticker,
                        "source": cik + ":" + accession,
                        "reason": "Latest revenue filing has no verified consolidated total for this period",
                        "attempts": attempts,
                    }
                    policy["unresolved"][period] = issue
                    issues.append({"period": period, **issue})

        approved = len(policy["periods"]) - previous_count
        report["companies"][ticker] = {"approved_periods": approved, "issues": issues}
        totals["approved_periods"] += approved
        totals["issues"] += len(issues)
        print(
            f"Revenue review: {ticker}: {approved} approved periods, {len(issues)} unresolved cases",
            flush=True,
        )

    policy["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    policy["periods"] = dict(sorted(policy["periods"].items()))
    policy["unresolved"] = dict(sorted(policy["unresolved"].items()))
    write_json(SUPPLEMENT_INDEX, supplements)
    write_json(RULES_PATH, policy)
    report["totals"] = dict(totals)
    if totals["issues"]:
        report["status"] = "completed_with_gaps"
    write_json(ROOT / "data/processed/sec-revenue-report.json", report)
    return report
