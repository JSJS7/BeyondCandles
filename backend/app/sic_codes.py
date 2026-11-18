"""SIC codes of the companies, from data/sic-codes.json. No database access.

Studies of ratios built on cash flow, assets or leverage leave financial firms out,
defined as companies with a SIC code from 6000 to 6999 (DEC-33). The file is written by
`review-sic-codes` from the cached SEC submissions files.
"""

from pathlib import Path

from app.policy_files import load_policy

SIC_CODES_PATH = Path(__file__).resolve().parents[2] / "data/sic-codes.json"
# One-digit SIC code 6: banks, insurers, brokers, real estate investment trusts and
# health insurers (UNH, CI, ELV), as in Ball et al. (2016) and Novy-Marx (2013).
FINANCIAL_SIC_FIRST = 6000
FINANCIAL_SIC_LAST = 6999


def is_financial_sic(sic):
    return FINANCIAL_SIC_FIRST <= sic <= FINANCIAL_SIC_LAST


def financial_tickers(path=SIC_CODES_PATH):
    """Tickers of the companies whose SIC code marks a financial firm."""
    document = load_policy(path, "sic-codes")
    tickers = set()
    for entry in document["companies"]:
        if is_financial_sic(entry["sic"]):
            tickers.add(entry["ticker"])
    return tickers
