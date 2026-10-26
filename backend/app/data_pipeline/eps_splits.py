"""Replay the frozen EPS share-basis review without downloading or changing data."""

import csv
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.annual_financials import annual_periods
from app.data_pipeline.sec import CACHE as SEC_CACHE
from app.data_pipeline.sec_statements import DocumentText
from app.data_pipeline.source_files import ROOT, SELECTION, read_json, sha256
from app.database import engine
from app.eps_splits import normalize_eps, sources_fingerprint, split_rules
from app.models import Company, FinancialFact

CACHE = ROOT / "data/raw/eps-splits"
ALIASES = {"FI": "FISV", "MMC": "MRSH", "BK": "BNY"}


def checked_reference(reference, directory, verified):
    path = directory / reference["file"]
    if path.parent != directory:
        raise ValueError("EPS split evidence must remain inside its source cache")
    identity = (path, reference["sha256"])
    if identity not in verified:
        if sha256(path) != reference["sha256"]:
            raise ValueError(f"EPS split evidence checksum mismatch: {path.name}")
        verified.add(identity)
    return path


def verify_evidence(policy):
    """Validate original vendor events, primary ratio evidence and the frozen price basis."""
    verified = set()
    vendor_path = checked_reference(policy["vendor_index"], CACHE, verified)
    vendor = read_json(vendor_path)
    expected = {row["Symbol"] for row in read_json(SELECTION)["companies"]}
    if set(vendor["companies"]) != expected or set(policy["companies"]) != expected:
        raise ValueError("EPS split coverage differs from the frozen 200 companies")
    if any(
        vendor[key] != policy[key]
        for key in ("basis_date", "selection_sha256", "price_manifest_sha256")
    ):
        raise ValueError("Vendor action snapshot does not match the reviewed EPS basis")
    observed = {}
    for ticker, reference in vendor["companies"].items():
        path = checked_reference(reference, CACHE, verified)
        body = read_json(path)["chart"]["result"][0]
        if body["meta"]["symbol"] != ALIASES.get(ticker, ticker):
            raise ValueError(f"Vendor symbol mismatch: {ticker}")
        for event in body.get("events", {}).get("splits", {}).values():
            day = datetime.fromtimestamp(event["date"], timezone.utc).date().isoformat()
            if day > policy["basis_date"]:
                raise ValueError("Vendor action exceeds the frozen share basis")
            observed[ticker, day] = (int(event["numerator"]), int(event["denominator"]))
    classified = {}
    for action in policy["actions"] + policy["excluded_vendor_events"]:
        key = action["ticker"], action["date"]
        if key in classified:
            raise ValueError("An EPS corporate action is classified twice")
        classified[key] = action["vendor_new_shares"], action["vendor_old_shares"]
    if classified != observed:
        raise ValueError("Vendor actions changed; repeat the EPS share-basis review")
    for action in policy["actions"]:
        evidence = action["ratio_evidence"]
        directory = SEC_CACHE if evidence["cache"] == "sec" else CACHE
        path = checked_reference(evidence, directory, verified)
        if evidence["cache"] != "sec":
            continue  # Issuer HTML disclosures are curated; their original bytes stay pinned.
        document = read_json(path)
        if str(document["cik"]).zfill(10) != action["source_cik"]:
            raise ValueError("Split disclosure belongs to a different SEC issuer")
        rows = document["facts"]["us-gaap"][evidence["tag"]]["units"]["pure"]
        ratio = Decimal(action["new_shares"]) / action["old_shares"]
        if (
            evidence["fact"] not in rows
            or Decimal(str(evidence["fact"]["val"])) != ratio
        ):
            raise ValueError(
                "Primary SEC split ratio does not match the approved action"
            )
    for ticker, approval in policy["companies"].items():
        for exchange in approval.get("share_exchanges", []):
            evidence = exchange["evidence"]
            path = checked_reference(evidence, SEC_CACHE, verified)
            parser = DocumentText()
            parser.feed(path.read_text(encoding="utf-8"))
            text = " ".join(" ".join(parser.parts).split())
            if evidence["quote"] not in text:
                raise ValueError(
                    f"Share exchange evidence does not state the reviewed ratio: {ticker}"
                )
    filing_index = checked_reference(policy["pre_split_filings"], CACHE, verified)
    for filing in read_json(filing_index).values():
        for reference in filing.values():
            checked_reference(reference, SEC_CACHE, verified)
    manifest = read_json(ROOT / "data/manifest.json")
    for reference in manifest["files"].values():
        path = ROOT / reference["file"]
        if sha256(path) != reference["sha256"]:
            raise ValueError("Frozen vendor price file changed")
        with path.open(encoding="utf-8", newline="") as stream:
            last = None
            for row in csv.DictReader(stream):
                last = row
        if last is None or last["Datetime"][:10] != policy["basis_date"]:
            raise ValueError(
                "Vendor price files do not share the approved split basis date"
            )
    return len(verified)


def verify_comparisons(actions, facts):
    """Replay rounded before/after reported EPS controls, independently of the selected output."""
    count = 0
    for action in actions:
        comparison = action.get("retrospective_comparison")
        if not comparison:
            continue
        for source in comparison.values():
            matching = []
            for fact in facts:
                if fact["metric"] != "eps_diluted":
                    continue
                same_source = True
                for key, expected in source.items():
                    actual = fact[key]
                    if key == "value":
                        equal = Decimal(str(actual)) == Decimal(expected)
                    else:
                        equal = str(actual) == str(expected)
                    if not equal:
                        same_source = False
                        break
                if same_source:
                    matching.append(fact)
            if not matching:
                raise ValueError(
                    "Reported EPS split control is absent from the source facts"
                )
        before, after = comparison["before"], comparison["after"]
        if not before["filed"] < action["date"] <= after["filed"]:
            raise ValueError("EPS split control must straddle the reviewed event")
        expected = (
            Decimal(before["value"]) * action["old_shares"] / action["new_shares"]
        )
        if abs(expected - Decimal(after["value"])) > Decimal("0.01"):
            raise ValueError("Reported EPS change does not agree with the split ratio")
        count += 1
    return count


def review_eps_splits():
    """Check every selected EPS against approval; return counts, leaving PostgreSQL untouched."""
    policy = split_rules()
    evidence_count = verify_evidence(policy)
    states = Counter()
    adjusted = 0
    adjusted_companies = set()
    comparisons = 0
    with engine.connect() as connection:
        companies = connection.execute(select(Company.__table__)).mappings().all()
        if {company["ticker"] for company in companies} != set(policy["companies"]):
            raise ValueError(
                "Stored company catalogue differs from the reviewed EPS catalogue"
            )
        for company in companies:
            facts = (
                connection.execute(
                    select(FinancialFact.__table__).where(
                        FinancialFact.company_id == company["id"]
                    )
                )
                .mappings()
                .all()
            )
            actions = [
                action
                for action in policy["actions"]
                if action["ticker"] == company["ticker"]
            ]
            comparisons += verify_comparisons(actions, facts)
            periods = annual_periods(facts)
            approval = policy["companies"].get(company["ticker"], {})
            if approval.get("observed_sha256") != sources_fingerprint(periods):
                raise ValueError(
                    f"EPS sources require a new share-basis review: {company['ticker']}"
                )
            normalized = normalize_eps(company["ticker"], periods, policy)
            for period in normalized:
                metric = period.metrics["eps_diluted"]
                states[metric.status] += 1
                if metric.eps_share_basis and metric.eps_share_basis.applied_splits:
                    adjusted += 1
                    adjusted_companies.add(company["ticker"])
    return {
        "status": "verified" if set(states) == {"ready"} else "needs_review",
        "basis_date": policy["basis_date"],
        "companies": len(companies),
        "eps_statuses": dict(states),
        "adjusted_periods": adjusted,
        "adjusted_companies": len(adjusted_companies),
        "physical_actions": len(policy["actions"]),
        "reported_eps_comparisons": comparisons,
        "excluded_vendor_events": len(policy["excluded_vendor_events"]),
        "verified_source_files": evidence_count,
    }
