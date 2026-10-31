"""Replay the frozen EPS share-basis review without downloading or changing data."""

import csv
import math
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import pairwise

from sqlalchemy import select

from app.annual_financials import annual_periods
from app.data_pipeline.benjaminpo import ALIASES
from app.data_pipeline.sec import CACHE as SEC_CACHE
from app.data_pipeline.sec_statements import DocumentText
from app.data_pipeline.source_files import (
    ROOT,
    SELECTION,
    read_json,
    sha256,
    write_json,
)
from app.database import engine
from app.eps_splits import (
    RULES_PATH,
    normalize_eps,
    sources_fingerprint,
    split_rules,
)
from app.models import Company, FinancialFact

CACHE = ROOT / "data/raw/eps-splits"
# The vendor may restate its latest sessions for a split before the first split-adjusted
# session (APH and MNST in 2026: three sessions earlier), so the check starts earlier.
SESSIONS_BEFORE_SPLIT = 10
SESSIONS_AFTER_SPLIT = 3
# A vendor ratio this close to 1 looks like an ordinary daily move, so continuity
# cannot tell whether the vendor restated its prices.
MIN_DECISIVE_RATIO = 1.25


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


def split_price_steps(action, closes):
    """Return sessions near the action whose close moves like an unrestated split.

    closes is an ascending list of (ISO date, close). A move counts as a step when its
    logarithm is closer to the vendor ratio than to no change: for 2:1 a fall of more
    than about 29% between two sessions.
    """
    first_split_session = None
    for index, (day, _close) in enumerate(closes):
        if day >= action["date"]:
            first_split_session = index
            break
    if first_split_session is None:
        raise ValueError(
            f"Vendor prices end before the split: {action['ticker']} {action['date']}"
        )
    start = max(first_split_session - SESSIONS_BEFORE_SPLIT - 1, 0)
    end = first_split_session + SESSIONS_AFTER_SPLIT + 1
    window = closes[start:end]
    expected = math.log(action["vendor_new_shares"] / action["vendor_old_shares"])
    steps = []
    for (_previous_day, previous_close), (day, close) in pairwise(window):
        change = math.log(previous_close / close)
        if abs(change - expected) < abs(change):
            steps.append(day)
    return steps


def verify_price_continuity(actions, closes_by_ticker):
    """Require each split to be restated in the vendor history or marked as not restated.

    An unmarked action must show no step; a marked one exactly one step on its
    vendor_prices_unadjusted_before date, which price_share_basis relies on.
    """
    checked = 0
    not_decisive = []
    marked_steps = 0
    for action in actions:
        label = f"{action['ticker']} {action['date']}"
        vendor_ratio = action["vendor_new_shares"] / action["vendor_old_shares"]
        if 1 / MIN_DECISIVE_RATIO < vendor_ratio < MIN_DECISIVE_RATIO:
            not_decisive.append(label)
            continue
        steps = split_price_steps(action, closes_by_ticker[action["ticker"]])
        marked = action.get("vendor_prices_unadjusted_before")
        expected_steps = [marked] if marked is not None else []
        if steps != expected_steps:
            raise ValueError(
                f"Vendor price steps {steps} around the split {label} differ from "
                f"the reviewed {expected_steps}"
            )
        checked += 1
        if marked is not None:
            marked_steps += 1
    return {
        "checked": checked,
        "vendor_steps_marked": marked_steps,
        "not_decisive": not_decisive,
    }


def vendor_closes(tickers):
    """Read frozen vendor closes by ticker; checksums are verified in verify_evidence."""
    manifest = read_json(ROOT / "data/manifest.json")
    result = {}
    for ticker in sorted(tickers):
        path = ROOT / manifest["files"][ticker]["file"]
        closes = []
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if not row["Close"]:
                    continue  # The vendor leaves a few sessions without prices.
                closes.append((row["Datetime"][:10], float(row["Close"])))
        result[ticker] = closes
    return result


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


def stored_companies(connection, policy):
    """Return the stored companies; their catalogue must be the reviewed EPS catalogue."""
    companies = connection.execute(select(Company.__table__)).mappings().all()
    if {company["ticker"] for company in companies} != set(policy["companies"]):
        raise ValueError(
            "Stored company catalogue differs from the reviewed EPS catalogue"
        )
    return companies


def stored_facts(connection, company):
    return (
        connection.execute(
            select(FinancialFact.__table__).where(
                FinancialFact.company_id == company["id"]
            )
        )
        .mappings()
        .all()
    )


def approve_eps_splits(approved_through):
    """Approve the share basis again after the window grew past approved_through.

    An approval pins a fingerprint of all selected EPS sources of a company, so new
    annual periods change every fingerprint. A new fingerprint is written only when
    the periods ending by approved_through still give the stored one: the reviewed
    part is unchanged and only later periods are added. Any changed company stops
    the approval without writing. Evidence, price continuity and the reported split
    controls are verified first, as in review_eps_splits.
    """
    policy = split_rules()
    verify_evidence(policy)
    closes = vendor_closes({action["ticker"] for action in policy["actions"]})
    continuity = verify_price_continuity(policy["actions"], closes)
    changed = []
    fingerprints = {}
    new_periods_before_splits = []
    with engine.connect() as connection:
        for company in stored_companies(connection, policy):
            ticker = company["ticker"]
            facts = stored_facts(connection, company)
            actions = [
                action for action in policy["actions"] if action["ticker"] == ticker
            ]
            verify_comparisons(actions, facts)
            periods = annual_periods(facts)
            reviewed_periods = [
                period for period in periods if period.period_end <= approved_through
            ]
            stored_fingerprint = policy["companies"][ticker]["observed_sha256"]
            if stored_fingerprint != sources_fingerprint(reviewed_periods):
                changed.append(ticker)
                continue
            fingerprints[ticker] = sources_fingerprint(periods)

            # List each new period ending before a split of its company: the
            # split is applied when the source was filed before it and is taken as
            # already restated when the source was filed on or after it.
            for period in periods:
                if period.period_end <= approved_through:
                    continue
                metric = period.metrics["eps_diluted"]
                if metric.status != "ready" or len(metric.selected_sources) != 1:
                    continue
                filed = metric.selected_sources[0].filed
                for action in actions:
                    split_day = date.fromisoformat(action["date"])
                    if period.period_end >= split_day:
                        continue
                    new_periods_before_splits.append(
                        {
                            "ticker": ticker,
                            "period_end": str(period.period_end),
                            "source_filed": str(filed),
                            "split": action["date"],
                            "split_applied": filed < split_day,
                        }
                    )
    if changed:
        raise ValueError(
            "EPS sources of already approved periods changed; review again: "
            + ", ".join(sorted(changed))
        )

    # The cached policy is shared with the API, so the file is re-read for writing.
    document = read_json(RULES_PATH)
    renewed = 0
    for ticker, fingerprint in fingerprints.items():
        approval = document["companies"][ticker]
        if approval["observed_sha256"] != fingerprint:
            approval["observed_sha256"] = fingerprint
            renewed += 1
    write_json(RULES_PATH, document)
    return {
        "status": "approved",
        "approved_through": str(approved_through),
        "companies": len(fingerprints),
        "renewed_fingerprints": renewed,
        "price_continuity": continuity,
        "new_periods_before_splits": new_periods_before_splits,
    }


def review_eps_splits():
    """Check every selected EPS against approval; return counts, leaving PostgreSQL untouched."""
    policy = split_rules()
    evidence_count = verify_evidence(policy)
    closes = vendor_closes({action["ticker"] for action in policy["actions"]})
    continuity = verify_price_continuity(policy["actions"], closes)
    states = Counter()
    adjusted = 0
    adjusted_companies = set()
    comparisons = 0
    with engine.connect() as connection:
        companies = stored_companies(connection, policy)
        for company in companies:
            facts = stored_facts(connection, company)
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
        "price_continuity": continuity,
        "verified_source_files": evidence_count,
    }
