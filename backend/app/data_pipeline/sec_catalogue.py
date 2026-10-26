"""Download and stream SEC cache entries for the frozen 200-company catalogue."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.data_pipeline.identifiers import (
    IDENTIFIERS,
    IssuerIdentifiers,
    cached,
    write_json,
)
from app.data_pipeline.sec import CACHE
from app.data_pipeline.source_files import SELECTION, read_json, sha256

ALL_INDEX = CACHE / "all-index.json"
DOWNLOAD_PROGRESS = CACHE / "all-progress.json"


def identifiers():
    document = read_json(IDENTIFIERS)
    if document["selection_sha256"] != sha256(SELECTION):
        raise ValueError("SEC identifiers use a different company selection")
    expected = {company["Symbol"] for company in read_json(SELECTION)["companies"]}
    if set(document["companies"]) != expected:
        raise ValueError("SEC identifiers must cover the frozen 200 companies")
    ciks = {entry["cik"] for entry in document["companies"].values()}
    if len(ciks) != len(expected):
        raise ValueError("Selected companies must have distinct SEC issuers")
    from .sec_history import validate_history_catalogue

    validate_history_catalogue(document["companies"])
    return document["companies"]


def verify_reference(reference):
    path = CACHE / reference["file"]
    if path.parent != CACHE or sha256(path) != reference["sha256"]:
        raise ValueError("SEC cache checksum mismatch")
    return path


def verify_submission(ticker, entry, submission):
    if str(submission["cik"]).zfill(10) != entry["cik"]:
        raise ValueError(f"Submission CIK mismatch: {ticker}")
    if ticker in submission.get("tickers", []):
        return
    if not entry.get("evidence"):
        raise ValueError(f"Missing historical SEC identity evidence: {ticker}")
    parser = IssuerIdentifiers()
    parser.feed(verify_reference(entry["evidence"]).read_text(encoding="utf-8"))
    ciks = {value.zfill(10) for value in parser.values["entitycentralindexkey"]}
    symbols = {value.replace(".", "-") for value in parser.values["tradingsymbol"]}
    if entry["cik"] not in ciks or ticker.replace(".", "-") not in symbols:
        raise ValueError(f"Historical identity evidence mismatch: {ticker}")


def fetch_all_sec(refresh=False):
    issuers = identifiers()
    progress: dict[str, Any] = (
        read_json(DOWNLOAD_PROGRESS)
        if DOWNLOAD_PROGRESS.exists()
        else {"companies": {}}
    )
    if refresh and not progress.get("pending_refresh"):
        progress = {"companies": {}, "pending_refresh": True}
        write_json(DOWNLOAD_PROGRESS, progress)
    refreshing = progress.get("pending_refresh", False)
    if ALL_INDEX.exists() and not refreshing:
        index, companies = load_all_sec()
        for _ in companies:
            pass
        from .sec_history import fetch_history_sources

        predecessors = fetch_history_sources(issuers)
        return {
            "status": "cached",
            "companies": len(index["companies"]),
            "predecessors": predecessors,
        }
    for ticker, issuer in issuers.items():
        completed = ticker in progress["companies"]
        old = progress["companies"].get(ticker, {})
        if issuer.get("evidence"):
            cached(issuer["evidence"]["url"], issuer["evidence"], json_response=False)
        previous_submission = issuer["submissions"]
        if refreshing:
            previous_submission = old.get("submissions") if completed else None
        submission_ref = cached(issuer["submissions"]["url"], previous_submission)
        submission = read_json(verify_reference(submission_ref))
        verify_submission(ticker, issuer, submission)
        previous_facts = old.get("facts")
        if refreshing and not completed:
            previous_facts = None
        facts_ref = cached(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{issuer['cik']}.json",
            previous_facts,
        )
        body = read_json(verify_reference(facts_ref))
        if str(body["cik"]).zfill(10) != issuer["cik"]:
            raise ValueError(f"SEC facts identity mismatch: {ticker}")
        retrieved_at = datetime.now(timezone.utc).isoformat()
        if previous_facts and previous_facts["sha256"] == facts_ref["sha256"]:
            retrieved_at = old.get("retrieved_at", retrieved_at)
        progress["companies"][ticker] = {
            **issuer,
            "submissions": submission_ref,
            "facts": facts_ref,
            "retrieved_at": retrieved_at,
        }
        write_json(DOWNLOAD_PROGRESS, progress)
        print(
            f"Cached SEC company: {ticker} ({len(progress['companies'])}/200)",
            flush=True,
        )
    index = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "companies": {ticker: progress["companies"][ticker] for ticker in issuers},
    }
    # A predecessor failure must leave the previous full snapshot and refresh state intact.
    from .sec_history import fetch_history_sources

    predecessors = fetch_history_sources(issuers, refresh=refreshing)
    write_json(ALL_INDEX, index)
    progress["pending_refresh"] = False
    write_json(DOWNLOAD_PROGRESS, progress)
    return {
        "status": "downloaded",
        "companies": len(issuers),
        "predecessors": predecessors,
    }


def load_all_sec():
    issuers = identifiers()
    index = read_json(ALL_INDEX)
    if set(index["companies"]) != set(issuers):
        raise ValueError("Full SEC cache does not match the frozen company selection")

    def companies():
        # Stream one response at a time rather than keeping 200 decoded JSONs in RAM.
        for ticker, entry in index["companies"].items():
            if entry["cik"] != issuers[ticker]["cik"]:
                raise ValueError(f"Cached CIK mismatch: {ticker}")
            submission = read_json(verify_reference(entry["submissions"]))
            verify_submission(ticker, issuers[ticker], submission)
            path = verify_reference(entry["facts"])
            body = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
            if str(body["cik"]).zfill(10) != entry["cik"]:
                raise ValueError(f"SEC facts identity mismatch: {ticker}")
            yield ticker, (entry, body)

    return index, companies()
