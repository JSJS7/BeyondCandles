"""Compare the screener's ratios with a reference built from report tables (K16).

`data/ratio-reference.json` holds a few companies valued on one date each: the inputs
read by hand from one annual report, the vendor's close and the ratios calculated by hand
from them. The check runs the screener on the stored database for each date and reports
every ratio further from the reference than the file's tolerance. A known difference
names its finding; one that no longer occurs is reported as unused, like the reviewed
flags of the other checks, so the file follows the fixes.
"""

from datetime import date
from decimal import Decimal

from app.data_pipeline.source_files import ROOT, read_json

REFERENCE = ROOT / "data/ratio-reference.json"
RATIO_NAMES = (
    "pe",
    "pb",
    "ps",
    "roe",
    "roa",
    "net_margin",
    "operating_margin",
    "revenue_growth",
    "eps_growth",
    "ocf_to_net_income",
    "fcf_margin",
    "equity_to_assets",
)


def load_reference():
    document = read_json(REFERENCE)
    if document["version"] != 1:
        raise ValueError("Unsupported ratio reference version")
    return document


def optional_decimal(text):
    if text is None:
        return None
    return Decimal(text)


def ratio_matches(expected, actual, tolerance):
    """Both empty, or both present and within the relative tolerance of the reference."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if expected == 0:
        return actual == 0
    relative_difference = abs(actual - expected) / abs(expected)
    return relative_difference <= tolerance


def case_differences(case, latest, tolerance):
    """Every ratio of one case that differs from the reference, or the wrong period.

    `latest` is the screener's period of the company (PeriodRatios), or None.
    """
    ticker = case["ticker"]
    valuation_date = case["valuation_date"]
    if latest is None:
        return [{"ticker": ticker, "valuation_date": valuation_date, "ratio": None}]
    expected_period_end = date.fromisoformat(case["period_end"])
    if latest.period_end != expected_period_end:
        return [
            {
                "ticker": ticker,
                "valuation_date": valuation_date,
                "ratio": "period_end",
                "expected": case["period_end"],
                "actual": str(latest.period_end),
            }
        ]
    differences = []
    for name in RATIO_NAMES:
        expected = optional_decimal(case["expected"][name])
        actual = getattr(latest.ratios, name)
        if ratio_matches(expected, actual, tolerance):
            continue
        differences.append(
            {
                "ticker": ticker,
                "valuation_date": valuation_date,
                "ratio": name,
                "expected": None if expected is None else str(expected),
                "actual": None if actual is None else str(actual),
            }
        )
    return differences


def separate_known_differences(differences, known):
    """Split differences into unexplained ones and those a finding explains.

    Returns (unexplained, explained, unused known entries).
    """

    def key(entry):
        return (entry["ticker"], entry["valuation_date"], entry["ratio"])

    known_by_key = {key(entry): entry for entry in known}
    unexplained = []
    explained = []
    used_keys = set()
    for difference in differences:
        entry = known_by_key.get(key(difference))
        if entry is None:
            unexplained.append(difference)
            continue
        explained.append({**difference, "finding": entry["finding"]})
        used_keys.add(key(difference))
    unused = [entry for entry in known if key(entry) not in used_keys]
    return unexplained, explained, unused


def check_ratios():
    """Run the screener for every reference date and compare the referenced rows."""
    from app.database import engine
    from app.screener import get_screener

    document = load_reference()
    tolerance = Decimal(document["tolerance"])
    differences = []
    with engine.connect() as connection:
        for case in document["cases"]:
            valuation_date = date.fromisoformat(case["valuation_date"])
            response = get_screener(connection, valuation_date=valuation_date)
            latest = None
            for row in response.rows:
                if row.ticker == case["ticker"]:
                    latest = row.latest
            differences.extend(case_differences(case, latest, tolerance))

    unexplained, explained, unused = separate_known_differences(
        differences, document["known_differences"]
    )
    compared = len(document["cases"]) * len(RATIO_NAMES)
    status = "verified"
    if unexplained or unused:
        status = "needs_review"
    return {
        "status": status,
        "cases": len(document["cases"]),
        "ratios_compared": compared,
        "tolerance": str(tolerance),
        "differences": unexplained,
        "known_differences": explained,
        "unused_known_differences": unused,
    }
