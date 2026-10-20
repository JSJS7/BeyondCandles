"""SEC pilot: retain reported facts rather than inventing normalized quarterly values."""
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.data_pipeline.prices import ROOT, number, read_json, sha256

PILOT = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "AMZN": "0001018724",
    "JPM": "0000019617",
    "JNJ": "0000200406",
    "XOM": "0000034088",
    "PG": "0000080424",
    "CAT": "0000018230",
    "NEE": "0000753308",
}
METRICS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "equity": ("StockholdersEquity",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
}
CACHE = ROOT / "data/raw/sec"
INDEX = CACHE / "index.json"
HISTORICAL_IDENTITIES = {
    "XOM": {
        "cik": "0000034088", "name": "EXXON MOBIL CORP",
        "evidence": "https://www.sec.gov/Archives/edgar/data/34088/000003408825000010/xom-20241231.htm",
        "note": "2024 10-K identifies XOM. Current submissions tickers are empty after 2026 successor reorganization; keep historical issuer for 2015-2024.",
    }
}


def verify_identity(ticker, cik, document):
    if str(document["cik"]).zfill(10) != cik:
        raise ValueError(f"SEC CIK mismatch: {ticker}")
    if ticker in document.get("tickers", []):
        return
    historical = HISTORICAL_IDENTITIES.get(ticker)
    if not historical or historical["cik"] != cik or historical["name"] != document.get("name"):
        raise ValueError(f"SEC identity mismatch: {ticker}")


def fetch(url, user_agent, json_response=True):
    for attempt in range(3):
        try:
            time.sleep(0.25)  # At most four requests/second, sequentially.
            with urlopen(Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"}), timeout=45) as response:
                body = response.read(100_000_001)
                if len(body) > 100_000_000:
                    raise ValueError("SEC response exceeds the per-file 100 MB guard")
                if json_response:
                    json.loads(body)  # Never cache an HTML error response as JSON.
                return body
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError(f"SEC HTTP {exc.code}; request stopped, no bypass attempted") from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise ValueError("SEC request failed after three attempts") from None
        time.sleep(2 ** attempt)
    raise RuntimeError("Unreachable")


def cache_response(url, user_agent, json_response=True):
    body = fetch(url, user_agent, json_response)
    digest = hashlib.sha256(body).hexdigest()
    path = CACHE / (digest + (".json" if json_response else ".htm"))
    if not path.exists() or sha256(path) != digest:
        path.write_bytes(body)
    return {"url": url, "sha256": digest, "file": path.name}


def fetch_sec(refresh=False):
    if INDEX.exists() and not refresh:
        load_pilot()  # Also verifies cached file hashes and identities.
        return {"status": "cached", "companies": len(PILOT)}
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        from app.core.config import settings
        user_agent = settings.sec_user_agent
    if not user_agent or "@" not in user_agent:
        raise ValueError("Set SEC_USER_AGENT to 'BeyondCandles contact@example.com' in local .env")
    CACHE.mkdir(parents=True, exist_ok=True)
    index = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "companies": {}}
    for ticker, cik in PILOT.items():
        submissions = cache_response(f"https://data.sec.gov/submissions/CIK{cik}.json", user_agent)
        document = read_json(CACHE / submissions["file"])
        verify_identity(ticker, cik, document)
        facts = cache_response(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", user_agent)
        index["companies"][ticker] = {
            "cik": cik,
            "name": document["name"],
            "submissions": submissions,
            "facts": facts,
            "historical_identity": HISTORICAL_IDENTITIES.get(ticker),
        }
        print(f"Cached SEC facts: {ticker}", flush=True)
    # Commit the index only when all ten companies have downloaded successfully.
    pending = CACHE / "index.pending.json"
    pending.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    pending.replace(INDEX)
    return {"status": "downloaded", "companies": len(PILOT)}


def load_pilot():
    index = read_json(INDEX)
    if set(index["companies"]) != set(PILOT):
        raise ValueError("SEC cache must cover exactly the ten pilot companies")
    result = {}
    for ticker, entry in index["companies"].items():
        if entry["cik"] != PILOT[ticker]:
            raise ValueError(f"CIK mismatch: {ticker}")
        for key in ("submissions", "facts"):
            path = CACHE / entry[key]["file"]
            if path.parent != CACHE or sha256(path) != entry[key]["sha256"]:
                raise ValueError(f"SEC cache checksum mismatch: {ticker} {key}")
        submission = read_json(CACHE / entry["submissions"]["file"])
        verify_identity(ticker, entry["cik"], submission)
        body = json.loads((CACHE / entry["facts"]["file"]).read_text(encoding="utf-8"), parse_float=Decimal)
        if str(body["cik"]).zfill(10) != entry["cik"]:
            raise ValueError(f"SEC facts identity mismatch: {ticker}")
        result[ticker] = (entry, body)
    return index, result


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


def extract_facts(body):
    facts = []
    warnings = []
    seen = {}
    taxonomy = body.get("facts", {}).get("us-gaap", {})
    for metric, tags in METRICS.items():
        expected_unit = "USD/shares" if metric == "eps_diluted" else "USD"
        for tag in tags:
            units = taxonomy.get(tag, {}).get("units", {})
            for unit, items in units.items():
                if unit != expected_unit:
                    warnings.append(f"Skipped non-target unit {tag}: {unit}")
                    continue
                for fact in items:
                    if not "2015-01-01" <= fact["end"] <= "2024-12-31" or fact.get("form") not in ("10-K", "10-K/A", "10-Q", "10-Q/A"):
                        continue
                    end = date.fromisoformat(fact["end"])
                    filed = date.fromisoformat(fact["filed"])
                    instant = metric == "equity"
                    if not instant and not fact.get("start"):
                        warnings.append(f"Missing start: {tag} {fact.get('accn')}")
                        continue
                    start = end if instant else date.fromisoformat(fact["start"])
                    if start > end or filed < end:
                        raise ValueError(f"Invalid SEC dates: {tag} {fact['accn']}")
                    value = number(str(fact["val"]), tag)
                    if abs(value) >= Decimal("1e28") or value.as_tuple().exponent < -10:
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
                        if any(seen[key][k] != row[k] for k in ("value", "filed", "form")):
                            raise ValueError(f"Ambiguous values inside one filing: {key}")
                        continue
                    seen[key] = row
                    facts.append(row)
    return facts, sorted(set(warnings))


def audit_sec():
    index, pilot = load_pilot()
    report = {"retrieved_at": index["retrieved_at"], "status": "pilot_audit", "limitations": [
        "Coverage means reported facts exist, not complete comparable quarterly financial statements.",
        "Alternative revenue/net-income tags are retained separately; no automatic summing or mixing definitions.",
        "Later filings may restate earlier periods. Historical queries must filter filed date and select an explicit filing version.",
        "SEC filing date is not necessarily the earlier earnings announcement date; intraday availability is not modeled.",
        "Annual source-document reconciliation must be completed separately; it is not certified by this automatic audit.",
        "No automatic Q4 subtraction, P/E calculation or split normalization is performed."
    ], "companies": {}}
    for ticker, (entry, body) in pilot.items():
        facts, warnings = extract_facts(body)
        metrics = {}
        for metric in METRICS:
            selected = [fact for fact in facts if fact["metric"] == metric]
            by_period = defaultdict(list)
            by_filing_period = defaultdict(list)
            for fact in selected:
                period = (fact["tag"], fact["unit"], fact["period_start"], fact["period_end"])
                filing_period = (fact["unit"], fact["period_start"], fact["period_end"], fact["accession"])
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
                "years": sorted(reported_years),
                "missing_years": sorted(set(range(2015, 2025)) - reported_years),
                "tags": sorted({fact["tag"] for fact in selected}),
                "period_kinds": dict(Counter(fact["period_kind"] for fact in selected)),
                "periods_with_changed_values": changed_periods,
                "filing_periods_with_differing_definitions": differing_definitions,
            }
            if differing_definitions:
                warnings.append(f"{metric}: alternative tags have different values within {differing_definitions} filing-periods; select an explicit definition before computing ratios")
        if ticker in HISTORICAL_IDENTITIES:
            warnings.append(HISTORICAL_IDENTITIES[ticker]["note"])
        report["companies"][ticker] = {
            "cik": entry["cik"],
            "name": entry["name"],
            "source_sha256": entry["facts"]["sha256"],
            "metrics": metrics,
            "warnings": warnings,
        }
    output = ROOT / "data/processed/sec-pilot-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def fact_key(fact):
    """Identify a reported fact independently of its download or numeric value."""
    return (
        fact["taxonomy"],
        fact["tag"],
        fact["unit"],
        fact["period_start"],
        fact["period_end"],
        fact["accession"],
    )


def import_sec():
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert
    from app.database import engine
    from app.models import Company, FinancialFact
    _, pilot = load_pilot()
    prepared = {}
    for ticker, (entry, body) in pilot.items():
        facts, _ = extract_facts(body)
        prepared[ticker] = (entry, facts)
    summary = {}
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(204812, 1)"))
        for ticker, (entry, facts) in prepared.items():
            company = connection.execute(select(Company.__table__).where(Company.ticker == ticker)).mappings().first()
            if not company:
                raise ValueError(f"Import the price catalogue first: {ticker}")
            if company["cik"] and company["cik"] != entry["cik"]:
                raise ValueError(f"Existing CIK conflict: {ticker}")
            connection.execute(Company.__table__.update().where(Company.id == company["id"]).values(cik=entry["cik"]))
            existing = connection.execute(select(FinancialFact.__table__).where(FinancialFact.company_id == company["id"])).mappings().all()
            existing_by_key = {fact_key(fact): fact for fact in existing}
            inserted = 0
            for offset in range(0, len(facts), 500):
                batch = []
                for fact in facts[offset:offset + 500]:
                    stored_fact = existing_by_key.get(fact_key(fact))
                    if stored_fact is not None:
                        if any(stored_fact[column] != value for column, value in fact.items()):
                            raise ValueError(f"Conflicting stored SEC fact: {ticker}")
                    else:
                        batch.append(dict(fact, company_id=company["id"]))
                if batch:
                    connection.execute(insert(FinancialFact), batch)
                    inserted += len(batch)
            result = {"ticker": ticker, "facts": len(facts), "inserted": inserted, "status": "completed"}
            summary[ticker] = result
    return summary
