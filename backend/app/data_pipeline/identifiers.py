"""Resolve the frozen company selection to SEC issuers, retaining identity evidence."""

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from app.data_pipeline.sec import CACHE, cache_response, verify_identity
from app.data_pipeline.source_files import ROOT, SELECTION, read_json, sha256

IDENTIFIERS = ROOT / "data/sec-identifiers.json"
PROGRESS = CACHE / "identity-progress.json"
# Confirmed by the 2024 10-K: /1390777/000139077725000046/bk-20241231.htm.
# The current SEC registry lists the same issuer as BNY.
HISTORICAL_CANDIDATES = {"BK": "0001390777"}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending.json")
    pending.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def user_agent():
    from app.core.config import settings

    if not settings.sec_user_agent or "@" not in settings.sec_user_agent:
        raise ValueError(
            "Set SEC_USER_AGENT with an application name and contact address"
        )
    return settings.sec_user_agent


def cached(url, previous=None, json_response=True):
    if previous and previous["url"] == url:
        path = CACHE / previous["file"]
        if (
            path.is_file()
            and path.parent == CACHE
            and sha256(path) == previous["sha256"]
        ):
            return previous
    return cache_response(url, user_agent(), json_response)


class IssuerIdentifiers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.values = {"tradingsymbol": set(), "entitycentralindexkey": set()}
        self.text = ""

    def handle_starttag(self, tag, attrs):
        name = (dict(attrs).get("name") or "").split(":")[-1].lower()
        if tag == "ix:nonnumeric" and name in self.values:
            self.current = name
            self.text = ""

    def handle_data(self, data):
        if self.current:
            self.text += data

    def handle_endtag(self, tag):
        if tag == "ix:nonnumeric" and self.current:
            self.values[self.current].add(self.text.strip())
            self.current = None


def annual_candidate(submission):
    """List the 2024 annual reports (10-K) in one block of SEC submission history."""
    candidates = []
    for index, form in enumerate(submission["form"]):
        report_date = submission["reportDate"][index]
        if form != "10-K" or not "2024-01-01" <= report_date <= "2024-12-31":
            continue
        candidates.append(
            {
                "report_date": report_date,
                "accession": submission["accessionNumber"][index],
                "document": submission["primaryDocument"][index],
            }
        )
    return candidates


def historical_evidence(ticker, cik, submission):
    candidates = annual_candidate(submission["filings"]["recent"])
    for older in submission["filings"]["files"]:
        if candidates:
            break
        if older["filingFrom"] <= "2025-12-31" and older["filingTo"] >= "2024-01-01":
            reference = cached("https://data.sec.gov/submissions/" + older["name"])
            candidates.extend(annual_candidate(read_json(CACHE / reference["file"])))
    if not candidates:
        raise ValueError(
            f"No 2024 annual filing to verify historical identity: {ticker}"
        )
    chosen = max(candidates, key=lambda item: (item["report_date"], item["accession"]))
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{chosen['accession'].replace('-', '')}/{chosen['document']}"
    reference = cached(url, json_response=False)
    parser = IssuerIdentifiers()
    parser.feed((CACHE / reference["file"]).read_text(encoding="utf-8"))
    reported_ciks = {
        value.zfill(10) for value in parser.values["entitycentralindexkey"]
    }
    reported_symbols = {
        value.replace(".", "-") for value in parser.values["tradingsymbol"]
    }
    if cik not in reported_ciks or ticker.replace(".", "-") not in reported_symbols:
        raise ValueError(f"Historical filing does not confirm ticker and CIK: {ticker}")
    return reference


def normalized_name(name):
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def resolve_sec():
    CACHE.mkdir(parents=True, exist_ok=True)
    selection = read_json(SELECTION)
    progress: dict[str, Any] = (
        read_json(PROGRESS) if PROGRESS.exists() else {"companies": {}}
    )
    selection_hash = sha256(SELECTION)
    approved = read_json(IDENTIFIERS) if IDENTIFIERS.exists() else {}
    if approved and approved.get("selection_sha256") != selection_hash:
        raise ValueError("Approved identifiers use a different company selection")
    if progress.get("selection_sha256", selection_hash) != selection_hash:
        raise ValueError("Identity checkpoint uses a different company selection")
    progress["selection_sha256"] = selection_hash
    registry_reference = cached(
        "https://www.sec.gov/files/company_tickers.json", progress.get("registry")
    )
    progress["registry"] = registry_reference
    registry = list(read_json(CACHE / registry_reference["file"]).values())
    unresolved = {}
    for company in selection["companies"]:
        ticker = company["Symbol"]
        if ticker in progress["companies"]:
            from .sec_catalogue import verify_reference, verify_submission

            entry = progress["companies"][ticker]
            submission = read_json(verify_reference(entry["submissions"]))
            if ticker not in submission.get("tickers", []) and not entry.get(
                "evidence"
            ):
                entry["evidence"] = historical_evidence(
                    ticker, entry["cik"], submission
                )
                entry["verification"] = "historical_filing"
                write_json(PROGRESS, progress)
            verify_submission(ticker, entry, submission)
            continue
        candidates = [
            item
            for item in registry
            if item["ticker"].replace(".", "-") == ticker.replace(".", "-")
        ]
        if ticker in HISTORICAL_CANDIDATES:
            cik = HISTORICAL_CANDIDATES[ticker]
        elif len(candidates) == 1:
            cik = str(candidates[0]["cik_str"]).zfill(10)
        else:
            candidates = [
                item
                for item in registry
                if normalized_name(item["title"])
                == normalized_name(company["Longname"])
            ]
            if len(candidates) != 1:
                unresolved[ticker] = "No unique SEC registry match"
                continue
            cik = str(candidates[0]["cik_str"]).zfill(10)
        reference = cached(f"https://data.sec.gov/submissions/CIK{cik}.json")
        submission = read_json(CACHE / reference["file"])
        if str(submission["cik"]).zfill(10) != cik:
            raise ValueError(f"Submission CIK mismatch: {ticker}")
        evidence = None
        try:
            verify_identity(ticker, cik, submission)
        except ValueError:
            evidence = historical_evidence(ticker, cik, submission)
        if ticker not in submission.get("tickers", []) and evidence is None:
            evidence = historical_evidence(ticker, cik, submission)
        progress["companies"][ticker] = {
            "cik": cik,
            "name": submission["name"],
            "submissions": reference,
            "verification": "historical_filing" if evidence else "verified_submission",
            "evidence": evidence,
        }
        write_json(PROGRESS, progress)
        print(
            f"Verified SEC issuer: {ticker} ({len(progress['companies'])}/200)",
            flush=True,
        )
    progress["unresolved"] = unresolved
    write_json(PROGRESS, progress)
    expected = {company["Symbol"] for company in selection["companies"]}
    if unresolved or set(progress["companies"]) != expected:
        return {
            "status": "needs_review",
            "resolved": len(progress["companies"]),
            "unresolved": unresolved,
        }
    if len({item["cik"] for item in progress["companies"].values()}) != len(expected):
        raise ValueError("Different selected companies resolve to the same SEC issuer")
    result = {
        "selection_sha256": sha256(SELECTION),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "registry": registry_reference,
        "companies": progress["companies"],
    }
    # Registry refreshes must not discard reviewed predecessor relationships.
    for ticker, previous in approved.get("companies", {}).items():
        if previous.get("history"):
            if result["companies"][ticker]["cik"] != previous["cik"]:
                raise ValueError(
                    f"Reviewed history requires a stable primary CIK: {ticker}"
                )
            result["companies"][ticker]["history"] = previous["history"]
    if approved.get("history_review"):
        result["history_review"] = approved["history_review"]
    write_json(IDENTIFIERS, result)
    return {"status": "verified", "companies": len(expected)}
