"""Publish each company's SIC code from its cached SEC submissions file (DEC-33, G2).

The backtest leaves financial firms out of the rankings of some ratios, as studies do
(SIC 6000-6999). The code lives in the `submissions` files of the SEC cache, outside Git,
so this command copies it into `data/sic-codes.json` together with the checksum of the
file it was read from. A changed cache then shows up as a changed file under --check
instead of silently moving a company into or out of the financial group.
"""

from app.data_pipeline.sec_catalogue import (
    ALL_INDEX,
    identifiers,
    verify_reference,
    verify_submission,
)
from app.data_pipeline.source_files import ROOT, read_json, write_json
from app.sic_codes import FINANCIAL_SIC_FIRST, FINANCIAL_SIC_LAST, is_financial_sic

SIC_CODES = ROOT / "data/sic-codes.json"
METHOD = (
    "The SIC code of each company as its SEC submissions file states it, read from the "
    "cached file named by its checksum. It is the current code, not a historical one, "
    "like the vendor's sector (I29). The backtest leaves companies with a code from "
    f"{FINANCIAL_SIC_FIRST} to {FINANCIAL_SIC_LAST} (financial firms) out of the rankings "
    "of the ratios listed in DEC-33."
)


def sic_entry(ticker, cik, submission, submissions_sha256):
    """One company's entry; a submissions file without a SIC code stops the command."""
    sic_text = str(submission.get("sic") or "").strip()
    if not sic_text.isdigit():
        raise ValueError(f"Submissions file has no SIC code: {ticker}")
    return {
        "ticker": ticker,
        "cik": cik,
        "sic": int(sic_text),
        "sic_description": submission.get("sicDescription") or "",
        "submissions_sha256": submissions_sha256,
    }


def sic_document(entries):
    """The published file: every company in ticker order and the size of the group."""
    companies = sorted(entries, key=lambda entry: entry["ticker"])
    financial = [entry for entry in companies if is_financial_sic(entry["sic"])]
    return {
        "version": 1,
        "method": METHOD,
        "financial_companies": len(financial),
        "companies": companies,
    }


def review_sic_codes():
    """Read the SIC code of every frozen company and publish data/sic-codes.json."""
    issuers = identifiers()
    index = read_json(ALL_INDEX)
    if set(index["companies"]) != set(issuers):
        raise ValueError("Full SEC cache does not match the frozen company selection")

    entries = []
    for ticker, cache_entry in sorted(index["companies"].items()):
        issuer = issuers[ticker]
        if cache_entry["cik"] != issuer["cik"]:
            raise ValueError(f"Cached CIK mismatch: {ticker}")
        reference = cache_entry["submissions"]
        submission = read_json(verify_reference(reference))
        verify_submission(ticker, issuer, submission)
        entry = sic_entry(ticker, issuer["cik"], submission, reference["sha256"])
        entries.append(entry)

    document = sic_document(entries)
    write_json(SIC_CODES, document)
    return {
        "status": "approved",
        "companies": len(document["companies"]),
        "financial_companies": document["financial_companies"],
        "file": "data/sic-codes.json",
    }
