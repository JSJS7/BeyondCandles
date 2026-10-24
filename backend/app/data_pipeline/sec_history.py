"""Apply reviewed SEC issuer-history rules without changing reported values."""

import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal

from app.data_pipeline.sec import CACHE, extract_facts
from app.data_pipeline.source_files import ROOT, read_json

HISTORY_INDEX = CACHE / "history-index.json"
RULE_FIELDS = ("filed_from", "filed_to", "period_end_from", "period_end_to")


def validate_rules(rules):
    """Reject misspelled fields and reversed bounds instead of ignoring them."""
    if not isinstance(rules, dict) or set(rules) - set(RULE_FIELDS):
        raise ValueError("Invalid SEC history eligibility rule")
    for value in rules.values():
        date.fromisoformat(value)
    for prefix in ("filed", "period_end"):
        lower = rules.get(prefix + "_from")
        upper = rules.get(prefix + "_to")
        if lower and upper and lower > upper:
            raise ValueError("Reversed SEC history date bounds")


def validate_history_catalogue(issuers):
    """Check explicit approvals and keep predecessors separate from other selected firms."""
    owners = {entry["cik"]: ticker for ticker, entry in issuers.items()}
    for ticker, entry in issuers.items():
        history = entry.get("history")
        if not history:
            continue
        if history.get("status") not in ("reviewed", "needs_review"):
            raise ValueError(f"Unrecognized SEC history status: {ticker}")
        if not history.get("note") or not history.get("evidence"):
            raise ValueError(
                f"SEC history requires a note and source evidence: {ticker}"
            )
        validate_rules(history.get("primary", {}))
        seen = {entry["cik"]}
        for source in history.get("predecessors", []):
            cik = source["cik"]
            if len(cik) != 10 or not cik.isascii() or not cik.isdigit() or cik in seen:
                raise ValueError(f"Invalid or repeated predecessor CIK: {ticker}")
            if cik in owners:
                raise ValueError(
                    f"Predecessor belongs to another selected company: {ticker}"
                )
            owners[cik] = ticker
            seen.add(cik)
            if not source.get("name") or not source.get("submissions"):
                raise ValueError(f"Missing predecessor identity: {ticker}")
            validate_rules(source.get("eligibility", {}))


def history_eligible(fact, primary_cik, history):
    """Evaluate source and date bounds; the filing date and fact period are independent."""
    if history and history["status"] != "reviewed":
        return False
    source_cik = fact["source_cik"]
    if source_cik == primary_cik:
        rules = history.get("primary", {}) if history else {}
    else:
        sources = history.get("predecessors", []) if history else []
        source = next((item for item in sources if item["cik"] == source_cik), None)
        if source is None:
            return False
        rules = source.get("eligibility", {})
    for field, value in rules.items():
        column, bound = field.rsplit("_", 1)
        boundary = date.fromisoformat(value)
        if bound == "from" and fact[column] < boundary:
            return False
        if bound == "to" and fact[column] > boundary:
            return False
    return True


def read_predecessor(source, index):
    """Verify both cached identities and hashes before accepting a predecessor response."""
    from .sec_catalogue import verify_reference

    entry = index.get("issuers", {}).get(source["cik"])
    if entry is None:
        raise ValueError(
            f"Missing predecessor cache: {source['cik']}; run fetch-sec --scope all"
        )
    submission = read_json(verify_reference(entry["submissions"]))
    if (
        str(submission["cik"]).zfill(10) != source["cik"]
        or submission["name"] != source["name"]
    ):
        raise ValueError(f"Predecessor identity mismatch: {source['cik']}")
    path = verify_reference(entry["facts"])
    body = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    if str(body["cik"]).zfill(10) != source["cik"]:
        raise ValueError(f"Predecessor facts CIK mismatch: {source['cik']}")
    return body


def prepare_company_facts(entry, body, history, index):
    """Retain all source facts and separately mark membership in the approved history."""
    sources = [(entry["cik"], body)]
    if history:
        from .sec_catalogue import verify_reference

        for evidence in history["evidence"]:
            verify_reference(evidence)
        for source in history.get("predecessors", []):
            sources.append((source["cik"], read_predecessor(source, index)))
    from .net_income import supporting_facts

    result = []
    for cik, response in sources:
        facts, _ = extract_facts(response)
        facts.extend(supporting_facts(response, facts))
        for fact in facts:
            fact["source_cik"] = cik
            fact["history_eligible"] = history_eligible(fact, entry["cik"], history)
            result.append(fact)
    from .revenue_facts import supplemental_facts

    for fact in supplemental_facts({cik for cik, _ in sources}):
        fact["history_eligible"] = history_eligible(fact, entry["cik"], history)
        result.append(fact)
    from .eps import supplemental_eps

    for fact in supplemental_eps({cik for cik, _ in sources}):
        fact["history_eligible"] = history_eligible(fact, entry["cik"], history)
        result.append(fact)
    return result


def fetch_history_sources(issuers, refresh=False):
    """Cache only configured predecessor issuers; preserve the published index on failure."""
    from .identifiers import cached, write_json
    from .sec_catalogue import verify_reference

    previous = read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    result = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "issuers": {}}
    for ticker, entry in issuers.items():
        history = entry.get("history")
        if not history:
            continue
        for evidence in history["evidence"]:
            downloaded = cached(evidence["url"], evidence, json_response=False)
            if downloaded["sha256"] != evidence["sha256"]:
                raise ValueError(f"Reviewed SEC evidence changed: {ticker}")
            verify_reference(evidence)
        for source in history.get("predecessors", []):
            cik = source["cik"]
            old = previous["issuers"].get(cik, {})
            submission = cached(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                None if refresh else old.get("submissions", source["submissions"]),
            )
            facts = cached(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                None if refresh else old.get("facts"),
            )
            result["issuers"][cik] = {
                "cik": cik,
                "name": source["name"],
                "submissions": submission,
                "facts": facts,
            }
            read_predecessor(source, result)
            print(f"Cached SEC predecessor: {ticker} {cik}", flush=True)
    if result["issuers"]:
        write_json(HISTORY_INDEX, result)
    return len(result["issuers"])


def audit_history():
    """Screen the frozen catalogue; report scope limits and unresolved period issues."""
    from app.annual_financials import annual_periods

    from .identifiers import IDENTIFIERS, write_json
    from .sec_catalogue import identifiers, load_all_sec

    issuers = identifiers()
    document = read_json(IDENTIFIERS)
    index = read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    _, companies = load_all_sec()
    report = {
        "status": "screened",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "All selected issuers screened for name history, annual gaps and overlapping periods; this is not a certification of every historical corporate action.",
        "deferred": document.get("history_review", {}).get("deferred", {}),
        "companies": {},
    }
    totals = Counter()
    for ticker, (entry, body) in companies:
        history = issuers[ticker].get("history")
        facts = prepare_company_facts(entry, body, history, index)
        eligible = [fact for fact in facts if fact["history_eligible"]]
        periods = annual_periods(eligible)
        submission = read_json(CACHE / entry["submissions"]["file"])
        unverified = any(period.fiscal_year is None for period in periods)
        fiscal_years = {
            period.fiscal_year for period in periods if period.fiscal_year is not None
        }
        missing = None if unverified else sorted(set(range(2015, 2025)) - fiscal_years)
        chronological = sorted(periods, key=lambda period: period.period_end)
        overlaps = []
        for earlier, later in zip(chronological, chronological[1:]):
            if later.period_start <= earlier.period_end:
                overlaps.append(
                    [
                        str(earlier.period_end),
                        str(later.period_start),
                        str(later.period_end),
                    ]
                )
        needs_review = bool(overlaps or unverified or (missing and len(periods) < 10))
        status = "needs_review" if needs_review else "screened"
        if history and history["status"] == "needs_review":
            status = "needs_review"
        if ticker in report["deferred"]:
            status = "deferred_period_review"
        report["companies"][ticker] = {
            "status": status,
            "cik": entry["cik"],
            "former_names": submission.get("formerNames", []),
            "note": history["note"] if history else None,
            "source_facts": len(facts),
            "history_facts": len(eligible),
            "excluded_facts": len(facts) - len(eligible),
            "annual_periods": len(periods),
            "fiscal_years": sorted(fiscal_years),
            "missing_fiscal_years": missing,
            "unverified_fiscal_years": unverified,
            "overlapping_periods": overlaps,
        }
        totals[status] += 1
        totals["source_facts"] += len(facts)
        totals["history_facts"] += len(eligible)
    report["totals"] = dict(totals)
    if totals["needs_review"]:
        report["status"] = "needs_review"
    write_json(ROOT / "data/processed/sec-history-report.json", report)
    return report
