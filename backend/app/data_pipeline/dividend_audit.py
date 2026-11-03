"""Check the dividends implied by Adjusted Close against SEC facts and a second source.

Adjusted Close / Close changes only on a distribution, so each change is a vendor event
with an implied cash amount. Every event is compared with the DoltHub dividend table, and
every fiscal year with the dividends per share the company reported to the SEC. The SEC
decides when the vendor and DoltHub disagree. Whatever stays unexplained needs a reviewed
entry in data/dividend-audit-reviews.json, pinned to the values it saw.
"""

import csv
import hashlib
import json
import statistics
import time
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.annual_financials import ANNUAL_REPORT_FORMS
from app.data_pipeline.price_adjustments import (
    FACTOR_NOISE,
    reviewed_price_events,
    vendor_factor_change,
)
from app.data_pipeline.source_files import ROOT, read_json, write_json
from app.data_pipeline.validation import number

REVIEWS = ROOT / "data/dividend-audit-reviews.json"
EVIDENCE_API = "https://www.dolthub.com/api/v1alpha1/post-no-preference/stocks/master"
# Dividend sources differ on ex-dates by up to about a week.
NEARBY_SESSIONS = 5
RELATIVE_TOLERANCE = Decimal("0.03")
EVENT_ABSOLUTE_TOLERANCE = Decimal("0.01")
YEAR_ABSOLUTE_TOLERANCE = Decimal("0.02")
# Payments of one year look alike when the largest is at most this multiple of the smallest.
SIMILAR_PAYMENTS = Decimal("1.25")
DIVIDEND_TAGS = (
    "CommonStockDividendsPerShareDeclared",
    "CommonStockDividendsPerShareCashPaid",
)
FIRST_ANNUAL_PERIOD_START = "2014-12-25"
LAST_ANNUAL_PERIOD_END = "2025-12-31"
SHORTEST_YEAR_DAYS = 340
LONGEST_YEAR_DAYS = 380
AMOUNT_PLACES = Decimal("0.0001")


def traded_price_factor(ticker, day, rules):
    """Return the factor that turns a vendor price on this day into the traded price.

    The vendor divides earlier prices by its own ratio for splits, spin-offs and
    conversions. An action marked vendor_prices_unadjusted_before was applied only from
    that day on, so earlier prices still equal the traded price.
    """
    factor = Decimal(1)
    for action in rules["actions"]:
        if action["ticker"] != ticker or day >= action["date"]:
            continue
        unadjusted_before = action.get("vendor_prices_unadjusted_before")
        if unadjusted_before is not None and day < unadjusted_before:
            continue
        factor *= Decimal(action["vendor_new_shares"]) / action["vendor_old_shares"]
    for event in rules["excluded_vendor_events"]:
        if event["ticker"] == ticker and day < event["date"]:
            factor *= Decimal(event["vendor_new_shares"]) / event["vendor_old_shares"]
    return factor


def share_ratio_between(ticker, after, through, rules):
    """Multiply the share-count ratios of splits with after < date <= through."""
    ratio = Decimal(1)
    for action in rules["actions"]:
        if action["ticker"] == ticker and after < action["date"] <= through:
            ratio *= Decimal(action["new_shares"]) / action["old_shares"]
    return ratio


def vendor_events(ticker, rows, rules, non_cash_dates=()):
    """List the sessions where the vendor's factor changes, with the implied traded cash."""
    events = []
    for index in range(1, len(rows)):
        change = vendor_factor_change(rows, index)
        if abs(change) <= FACTOR_NOISE or rows[index]["date"] in non_cash_dates:
            continue
        previous = rows[index - 1]
        vendor_basis_cash = number(previous["close"], "prior Close") * (
            1 - 1 / (1 + change)
        )
        factor = traded_price_factor(ticker, previous["date"], rules)
        events.append(
            {
                "index": index,
                "date": rows[index]["date"],
                "cash": vendor_basis_cash * factor,
            }
        )
    return events


def is_within(first, second, absolute_tolerance):
    allowed = max(RELATIVE_TOLERANCE * abs(second), absolute_tolerance)
    return abs(first - second) <= allowed


def format_amount(amount):
    return None if amount is None else str(amount.quantize(AMOUNT_PLACES))


def compare_with_source(events, source_rows, dates):
    """Pair source dividends with vendor events; return differences and the matched count."""
    differences = []
    used = set()
    matched = 0
    for row in source_rows:
        amount = Decimal(row["amount"])
        position = bisect_left(dates, row["ex_date"])
        nearby = [
            event
            for event in events
            if event["index"] not in used
            and abs(event["index"] - position) <= NEARBY_SESSIONS
        ]
        if not nearby:
            differences.append(_difference("source_only", row["ex_date"], None, amount))
            continue
        event = min(
            nearby,
            key=lambda item: (
                abs(item["cash"] - amount),
                abs(item["index"] - position),
            ),
        )
        used.add(event["index"])
        if is_within(event["cash"], amount, EVENT_ABSOLUTE_TOLERANCE):
            matched += 1
        else:
            differences.append(
                _difference("amount", event["date"], event["cash"], amount)
            )
    for event in events:
        if event["index"] not in used:
            differences.append(
                _difference("vendor_only", event["date"], event["cash"], None)
            )
    return differences, matched


def _difference(check, day, vendor, source):
    return {"check": check, "date": day, "vendor": vendor, "source": source}


def close_pair_dates(events):
    """List events much closer to the previous one than the company's usual interval.

    A duplicated dividend does not change an ordinary run of equal payments, so it is
    invisible in the amounts: only the interval between the two shows it.
    """
    if len(events) < 4:
        return set()
    gaps = [
        current["index"] - previous["index"]
        for previous, current in zip(events, events[1:], strict=False)
    ]
    usual_gap = statistics.median(gaps)
    return {
        current["date"]
        for previous, current in zip(events, events[1:], strict=False)
        if current["index"] - previous["index"] < usual_gap / 2
    }


def usual_payments_per_year(events):
    """Return the median number of events in a calendar year that has any."""
    counts = defaultdict(int)
    for event in events:
        counts[event["date"][:4]] += 1
    return max(1, round(statistics.median(counts.values()))) if counts else 1


def on_period_basis(ticker, event, period_end, rules):
    """Express an event's cash on the share basis at the end of a fiscal period."""
    if event["date"] <= period_end:
        return event["cash"] / share_ratio_between(
            ticker, event["date"], period_end, rules
        )
    return event["cash"] * share_ratio_between(ticker, period_end, event["date"], rules)


def explain_fiscal_year(ticker, events, period, reported, usual_count, rules):
    """Compare one fiscal year; return its vendor total, closest SEC value and explanation.

    direct: the year's events add up to a reported value.
    single_payment: the tagged value equals one of several similar payments in the year.
    timing: declared and paid dividends differ from ex-dates by one payment across the
    year end, so a run of the usual number of payments shifted by one matches instead.
    mismatch: none of these.
    """
    start, end = period
    in_year = [event for event in events if start <= event["date"] <= end]
    amounts = [on_period_basis(ticker, event, end, rules) for event in in_year]
    total = sum(amounts, Decimal(0))
    closest = min(reported, key=lambda value: abs(value - total))
    if is_within(total, closest, YEAR_ABSOLUTE_TOLERANCE):
        return total, closest, "direct"

    if len(amounts) >= 2 and min(amounts) > 0:
        similar = max(amounts) <= SIMILAR_PAYMENTS * min(amounts)
        if similar and any(
            is_within(value, amount, YEAR_ABSOLUTE_TOLERANCE)
            for value in reported
            for amount in amounts
        ):
            return total, closest, "single_payment"

    first = next(
        (position for position, event in enumerate(events) if event["date"] >= start),
        len(events),
    )
    for run_start in (first - 1, first, first + 1):
        run = events[max(run_start, 0) : max(run_start, 0) + usual_count]
        if len(run) != usual_count:
            continue
        run_total = sum(
            (on_period_basis(ticker, event, end, rules) for event in run), Decimal(0)
        )
        if any(is_within(run_total, v, YEAR_ABSOLUTE_TOLERANCE) for v in reported):
            return total, closest, "timing"
    return total, closest, "mismatch"


def audit_company(ticker, rows, source_rows, annual_dividends, rules, non_cash_dates):
    """Run all comparisons for one company; return its flags and counts."""
    events = vendor_events(ticker, rows, rules, non_cash_dates)
    dates = [row["date"] for row in rows]
    in_window = [
        row
        for row in source_rows
        if dates[0] < row["ex_date"] <= dates[-1] and Decimal(row["amount"]) > 0
    ]
    differences, matched = compare_with_source(events, in_window, dates)
    close_pairs = close_pair_dates(events)
    usual_count = usual_payments_per_year(events)

    years = {}
    explanations = defaultdict(int)
    flags = []
    for period, reported in sorted(annual_dividends.items()):
        total, closest, explanation = explain_fiscal_year(
            ticker, events, period, reported, usual_count, rules
        )
        years[period] = explanation
        explanations[explanation] += 1
        if explanation == "mismatch":
            flags.append(
                {
                    "ticker": ticker,
                    "check": "fiscal_year",
                    "date": period[1],
                    "vendor": format_amount(total),
                    "source": format_amount(closest),
                }
            )

    def confirmed_by_year(day):
        return any(
            start <= day <= end and explanation == "direct"
            for (start, end), explanation in years.items()
        )

    regular_without_confirmation = 0
    for difference in differences:
        if confirmed_by_year(difference["date"]):
            continue
        is_regular = difference["check"] == "vendor_only" and _equals_neighbour(
            events, difference["date"]
        )
        if is_regular and difference["date"] not in close_pairs:
            regular_without_confirmation += 1
            continue
        flags.append(
            {
                "ticker": ticker,
                "check": difference["check"],
                "date": difference["date"],
                "vendor": format_amount(difference["vendor"]),
                "source": format_amount(difference["source"]),
            }
        )

    for event in events:
        if event["date"] in close_pairs and not confirmed_by_year(event["date"]):
            flags.append(
                {
                    "ticker": ticker,
                    "check": "close_pair",
                    "date": event["date"],
                    "vendor": format_amount(event["cash"]),
                    "source": None,
                }
            )

    return {
        "flags": flags,
        "events": len(events),
        "source_rows": len(in_window),
        "matched": matched,
        "regular_without_confirmation": regular_without_confirmation,
        "years": dict(explanations),
    }


def _equals_neighbour(events, day):
    """Tell whether an event repeats the amount of the event before or after it."""
    for position, event in enumerate(events):
        if event["date"] != day:
            continue
        neighbours = (
            events[max(position - 1, 0) : position]
            + events[position + 1 : position + 2]
        )
        return any(
            is_within(event["cash"], neighbour["cash"], EVENT_ABSOLUTE_TOLERANCE)
            for neighbour in neighbours
        )
    return False


def separate_reviewed_flags(flags, reviewed):
    """Split flags into reviewed and unreviewed, and report reviews no flag matches.

    A review covers only the values it saw, so changed prices make the flag unreviewed
    again. A review without a flag is reported too: its disappearance is a data change.
    """
    by_key = {(item["ticker"], item["check"], item["date"]): item for item in reviewed}
    unreviewed = []
    matched = set()
    for flag in flags:
        key = (flag["ticker"], flag["check"], flag["date"])
        review = by_key.get(key)
        if (
            review
            and review["vendor"] == flag["vendor"]
            and review["source"] == flag["source"]
        ):
            matched.add(key)
        else:
            unreviewed.append(flag)
    not_found = [
        {"ticker": ticker, "check": check, "date": day}
        for ticker, check, day in sorted(set(by_key) - matched)
    ]
    return unreviewed, len(matched), not_found


def source_rows_for(ticker, rows_by_symbol, symbol_history):
    """Return the source rows that belong to this company, across its old tickers.

    A ticker can belong to another company at other dates, so a listed company takes rows
    only from the reviewed date ranges; any other company uses its own ticker throughout.
    """
    ranges = symbol_history.get(ticker)
    if ranges is None:
        return list(rows_by_symbol.get(ticker, []))
    result = []
    for item in ranges:
        for row in rows_by_symbol.get(item["symbol"], []):
            if item["from"] <= row["ex_date"] <= item["through"]:
                result.append(row)
    return sorted(result, key=lambda row: row["ex_date"])


def annual_dividend_facts(body):
    """Return reported annual dividends per share, by period and filing date."""
    facts = defaultdict(list)
    gaap = body.get("facts", {}).get("us-gaap", {})
    for tag in DIVIDEND_TAGS:
        units = gaap.get(tag, {}).get("units", {})
        for fact in units.get("USD/shares", []):
            start = fact.get("start")
            if not start or fact.get("form") not in ANNUAL_REPORT_FORMS:
                continue
            end = fact["end"]
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if (
                SHORTEST_YEAR_DAYS < days < LONGEST_YEAR_DAYS
                and start >= FIRST_ANNUAL_PERIOD_START
                and end <= LAST_ANNUAL_PERIOD_END
            ):
                facts[(start, end)].append(
                    (number(str(fact["val"]), tag), fact["filed"])
                )
    return facts


def reported_dividends(ticker, bodies, rules):
    """Merge a company's issuers; put later restated values on the period-end share basis."""
    result = defaultdict(list)
    for body in bodies:
        for period, facts in annual_dividend_facts(body).items():
            for value, filed in facts:
                ratio = share_ratio_between(ticker, period[1], filed, rules)
                result[period].append(value * ratio)
    return dict(result)


def read_price_rows(path):
    """Read only the columns the comparison needs, grouped by company."""
    rows = defaultdict(list)
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows[row["ticker"]].append(
                {
                    "date": row["date"],
                    "close": row["close"],
                    "adjusted_close": row["adjusted_close"],
                }
            )
    return rows


def evidence_rows_digest(rows):
    text = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_evidence(document, tickers):
    """Download the pinned DoltHub rows one symbol at a time and publish one file."""
    evidence = document["evidence"]
    symbols = set(tickers)
    for ranges in document["symbol_history"].values():
        symbols.update(item["symbol"] for item in ranges)
    rows = []
    for symbol in sorted(symbols):
        rows.extend(_fetch_symbol(symbol, evidence["revision"]))
    if evidence_rows_digest(rows) != evidence["rows_sha256"]:
        raise ValueError("Downloaded dividend rows differ from the reviewed evidence")
    write_json(
        ROOT / evidence["file"],
        {
            "revision": evidence["revision"],
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows": rows,
        },
    )


def _fetch_symbol(symbol, revision):
    query = (
        f"SELECT * FROM dividend AS OF '{revision}' WHERE act_symbol='{symbol}' "
        "AND ex_date>='2014-12-01' AND ex_date<='2026-01-31' ORDER BY ex_date"
    )
    request = Request(
        f"{EVIDENCE_API}?{urlencode({'q': query})}",
        headers={"User-Agent": "BeyondCandles historical data preparation"},
    )
    last_error = None
    for _ in range(4):
        try:
            with urlopen(request, timeout=45) as response:
                body = json.load(response)
            if body["query_execution_status"] == "Success":
                return body["rows"]
            last_error = body["query_execution_message"]
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(3)
    raise ValueError(f"DoltHub dividend query failed for {symbol}: {last_error}")


def load_evidence(document):
    """Read the pinned source rows, grouped by symbol."""
    evidence = document["evidence"]
    rows = read_json(ROOT / evidence["file"])["rows"]
    if evidence_rows_digest(rows) != evidence["rows_sha256"]:
        raise ValueError("Dividend evidence differs from the reviewed rows")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["act_symbol"]].append(row)
    return grouped


def audit_dividends(price_path, reported, rules, *, fetch=False):
    """Compare all prepared histories; report what no source and no review explains."""
    document = read_json(REVIEWS)
    if document["version"] != 1:
        raise ValueError("Unsupported dividend audit reviews version")
    prices = read_price_rows(price_path)
    if fetch and not (ROOT / document["evidence"]["file"]).exists():
        fetch_evidence(document, sorted(prices))
    rows_by_symbol = load_evidence(document)

    non_cash = defaultdict(set)
    for (ticker, day), event in reviewed_price_events().items():
        if event["kind"] != "cash_dividend":
            non_cash[ticker].add(day)

    flags = []
    totals = {
        "events": 0,
        "source_rows": 0,
        "matched": 0,
        "regular_without_confirmation": 0,
    }
    years = defaultdict(int)
    without_sec_years = 0
    for ticker, rows in prices.items():
        company_years = reported.get(ticker, {})
        without_sec_years += not company_years
        result = audit_company(
            ticker,
            rows,
            source_rows_for(ticker, rows_by_symbol, document["symbol_history"]),
            company_years,
            rules,
            non_cash[ticker],
        )
        flags.extend(result["flags"])
        for name in totals:
            totals[name] += result[name]
        for explanation, count in result["years"].items():
            years[explanation] += count

    unreviewed, reviewed_count, not_found = separate_reviewed_flags(
        flags, document["flags"]
    )
    return {
        "status": "needs_review" if unreviewed or not_found else "passed",
        **totals,
        "fiscal_years": dict(years),
        "companies_without_sec_dividends": without_sec_years,
        "reviewed_flags": reviewed_count,
        "unreviewed_flags": unreviewed,
        "reviews_not_found": not_found,
    }
