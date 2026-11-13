"""Apply reviewed corporate-action corrections to the frozen Kaggle snapshot."""

from decimal import Decimal, localcontext

from app.data_pipeline.source_files import ROOT, read_json
from app.data_pipeline.validation import number

PRICE_QUANTUM = Decimal("1e-18")
# The vendor's factor moves by less than this on sessions without an event (rounding of
# its single-precision prices; measured below 1e-5 across the 2015-2025 window).
FACTOR_NOISE = Decimal("0.00001")
# A reviewed vendor amount may differ from the implied cash by the vendor's float noise.
REVIEWED_AMOUNT_TOLERANCE = Decimal("0.0005")
# Dividend sources differ on ex-dates by up to about a week.
NEARBY_SESSIONS = 5
REVIEWED_EVENTS = ROOT / "data/price-audit-reviews.json"


def cash_adjustment(close, amount):
    """Return the multiplier applied strictly before the ex-dividend session."""
    if amount <= 0 or amount >= close:
        raise ValueError(
            "A cash distribution must be positive and smaller than prior Close"
        )

    return (close - amount) / close


def apply_adjustments(rows, rule):
    """Retain source Close; change only the independently stored Adjusted Close."""
    rows = sorted(rows, key=lambda row: row["date"])
    positions = {row["date"]: index for index, row in enumerate(rows)}

    if len(positions) != len(rows):
        raise ValueError("Duplicate price correction session")

    with localcontext() as context:
        context.prec = 50

        if rule["method"] == "cash_dividends":
            return rebuild_dividends(rows, positions, rule["dividends"])

        if rule["method"] == "remove_duplicate_distribution":
            return remove_duplicate_distribution(rows, positions, rule)

        if rule["method"] == "missing_cash_dividends":
            return add_missing_dividends(rows, positions, rule["dividends"])

        if rule["method"] == "reviewed_dividend_changes":
            return change_vendor_dividends(rows, positions, rule["changes"])

        if rule["method"] == "unrecorded_share_exchange":
            return restate_before_share_exchange(rows, positions, rule)

    raise ValueError(f"Unknown price correction method: {rule['method']}")


def rebuild_dividends(rows, positions, dividends):
    multipliers = {}

    for dividend in dividends:
        day = dividend["ex_date"]
        position = positions.get(day)

        if position is None or position == 0:
            raise ValueError(f"Missing ex-dividend session or prior Close: {day}")

        if day in multipliers:
            raise ValueError(f"Duplicate cash distribution: {day}")

        amount = number(dividend["amount"], "dividend")
        divisor = number(dividend["split_divisor"], "dividend split divisor")
        if divisor <= 0:
            raise ValueError("Dividend split divisor must be positive")

        previous_close = number(rows[position - 1]["close"], "prior Close")
        multipliers[day] = cash_adjustment(previous_close, amount / divisor)

    factor = Decimal(1)
    corrected = {}

    # Close already includes the stock split. Adjust only for cash distributions,
    # using dividend amounts expressed on that same share basis.
    for row in reversed(rows):
        day = row["date"]
        close = number(row["close"], "Close")
        corrected[day] = (close * factor).quantize(PRICE_QUANTUM)

        if day in multipliers:
            factor *= multipliers[day]

    return {row["date"]: corrected[row["date"]] for row in rows}


def remove_duplicate_distribution(rows, positions, rule):
    day = rule["ex_date"]
    position = positions.get(day)

    if position is None or position == 0:
        raise ValueError(f"Missing distribution session or prior Close: {day}")

    previous = rows[position - 1]
    current = rows[position]
    previous_close = number(previous["close"], "prior Close")
    amount = number(rule["amount"], "duplicate distribution")
    duplicate_factor = cash_adjustment(previous_close, amount)

    previous_adjusted = number(previous["adjusted_close"], "prior Adjusted Close")
    current_adjusted = number(current["adjusted_close"], "Adjusted Close")
    current_close = number(current["close"], "Close")
    previous_ratio = previous_adjusted / previous_close
    current_ratio = current_adjusted / current_close
    implied_cash = previous_close * (1 - previous_ratio / current_ratio)

    # Never remove a jump merely because it is large: the original series must
    # exhibit the specific reviewed duplicate distribution, allowing float noise.
    if abs(implied_cash - amount) > Decimal("0.0001"):
        raise ValueError(
            "Source prices do not match the reviewed duplicate distribution"
        )

    corrected = {}
    for row in rows:
        adjusted = number(row["adjusted_close"], "Adjusted Close")
        if row["date"] < day:
            adjusted /= duplicate_factor
        corrected[row["date"]] = adjusted.quantize(PRICE_QUANTUM)

    return corrected


def vendor_factor_change(rows, index):
    """Relative change of the vendor's Adjusted Close / Close factor into one session."""
    previous = rows[index - 1]
    current = rows[index]
    previous_factor = number(previous["adjusted_close"], "prior Adjusted Close") / (
        number(previous["close"], "prior Close")
    )
    current_factor = number(current["adjusted_close"], "Adjusted Close") / number(
        current["close"], "Close"
    )
    return current_factor / previous_factor - 1


def add_missing_dividends(rows, positions, dividends):
    """Apply reviewed cash dividends absent from the vendor's Adjusted Close.

    Each multiplies every earlier Adjusted Close by (prior Close - amount) / prior
    Close; Close and the vendor's other factors stay as they are.
    """
    multipliers = {}

    for dividend in dividends:
        day = dividend["ex_date"]
        position = positions.get(day)

        if position is None or position == 0:
            raise ValueError(f"Missing ex-dividend session or prior Close: {day}")

        if day in multipliers:
            raise ValueError(f"Duplicate cash distribution: {day}")

        previous_close = number(rows[position - 1]["close"], "prior Close")
        amount = number(dividend["amount"], "dividend")
        divisor = number(dividend["split_divisor"], "dividend split divisor")
        if divisor <= 0:
            raise ValueError("Dividend split divisor must be positive")
        multiplier = cash_adjustment(previous_close, amount / divisor)

        # The vendor must not have this dividend already: not on the ex-date (any
        # change there, beyond rounding noise, is something else too) and not a few
        # sessions away, since sources differ on ex-dates by days.
        first = max(position - NEARBY_SESSIONS, 1)
        last = min(position + NEARBY_SESSIONS, len(rows) - 1)
        for index in range(first, last + 1):
            change = abs(vendor_factor_change(rows, index))
            if index == position and change > FACTOR_NOISE:
                raise ValueError(
                    f"Source already adjusts the ex-dividend session: {day}"
                )
            if change > (1 - multiplier) / 2:
                raise ValueError(
                    "Source adjusts a session near the ex-dividend date "
                    f"{day}: {rows[index]['date']}"
                )
        multipliers[day] = multiplier

    return multiply_earlier_sessions(rows, multipliers)


def change_vendor_dividends(rows, positions, changes):
    """Replace cash amounts the vendor applied wrongly, keeping its other factors.

    Each change names the amount the vendor's factor implies on its ex-date (0 when it
    has no event there) and the correct amount on the vendor's Close basis (amount /
    split_divisor; 0 removes the event). The vendor's series must show exactly that
    amount, so a review can never alter a session it did not look at.
    """
    multipliers = {}

    for change in changes:
        day = change["ex_date"]
        position = positions.get(day)

        if position is None or position == 0:
            raise ValueError(f"Missing dividend change session or prior Close: {day}")

        if day in multipliers:
            raise ValueError(f"Duplicate dividend change: {day}")

        previous_close = number(rows[position - 1]["close"], "prior Close")
        vendor_multiplier = 1 / (1 + vendor_factor_change(rows, position))
        implied_cash = previous_close * (1 - vendor_multiplier)
        expected = number(change["vendor_amount"], "vendor dividend amount")
        if abs(implied_cash - expected) > REVIEWED_AMOUNT_TOLERANCE:
            raise ValueError(
                f"Source does not show the reviewed amount on {day}: {implied_cash}"
            )

        amount = number(change["amount"], "dividend")
        divisor = number(change["split_divisor"], "dividend split divisor")
        if divisor <= 0:
            raise ValueError("Dividend split divisor must be positive")
        correct_multiplier = Decimal(1)
        if amount != 0:
            correct_multiplier = cash_adjustment(previous_close, amount / divisor)
        multipliers[day] = correct_multiplier / vendor_multiplier

    return multiply_earlier_sessions(rows, multipliers)


def restate_before_share_exchange(rows, positions, rule):
    """Put Adjusted Close before a share exchange the vendor never recorded on new shares.

    Charter's old shares became 0.9042 new shares each on 18 May 2016 (N120). The vendor
    has no event that day, so its Adjusted Close rises by the exchange as well as by the
    market. Earlier sessions are divided by the share ratio, as analysis sites restate
    such an exchange; Close keeps the traded price of the old shares. The vendor must
    show no factor change of its own and a step in the direction of the exchange.
    """
    day = rule["date"]
    position = positions.get(day)
    if position is None or position == 0:
        raise ValueError(f"Missing share exchange session or prior Close: {day}")
    if abs(vendor_factor_change(rows, position)) > FACTOR_NOISE:
        raise ValueError(f"The vendor already adjusted its prices on {day}")
    ratio = Decimal(rule["new_shares"]) / Decimal(rule["old_shares"])
    previous_close = number(rows[position - 1]["close"], "prior Close")
    close = number(rows[position]["close"], "Close")
    # As review-eps-splits finds the step: the move is closer to the ratio than to none.
    move = (previous_close / close).ln()
    if abs(move - ratio.ln()) >= abs(move):
        raise ValueError(f"Prices do not step by the share exchange on {day}")
    return multiply_earlier_sessions(rows, {day: 1 / ratio})


def multiply_earlier_sessions(rows, multipliers):
    """Multiply Adjusted Close of the sessions before each dated multiplier."""
    factor = Decimal(1)
    corrected = {}

    for row in reversed(rows):
        day = row["date"]
        adjusted = number(row["adjusted_close"], "Adjusted Close")
        corrected[day] = (adjusted * factor).quantize(PRICE_QUANTUM)

        if day in multipliers:
            factor *= multipliers[day]

    return {row["date"]: corrected[row["date"]] for row in rows}


def scan_adjustment_factors(rows):
    """Flag material factor changes for inspection; never clip market returns."""
    previous = {}
    companies = {}
    material_events = []

    for row in rows:
        ticker = row["ticker"]
        day = row["date"]
        close = number(row["close"], "Close")
        factor = number(row["adjusted_close"], "Adjusted Close") / close
        company = companies.setdefault(ticker, {"last_adjustment": None, "sessions": 0})
        company["sessions"] += 1

        if ticker in previous:
            previous_day, previous_factor = previous[ticker]
            if day <= previous_day:
                raise ValueError(
                    f"Price audit requires chronological sessions: {ticker}"
                )

            change = factor / previous_factor - 1
            if abs(change) > Decimal("0.00001"):
                company["last_adjustment"] = day
            if abs(change) > Decimal("0.03"):
                material_events.append(
                    {
                        "ticker": ticker,
                        "date": day,
                        "factor_change_percent": str(change * 100),
                    }
                )

        previous[ticker] = day, factor

    return companies, material_events


def find_dividend_coverage_gap(statistics, body, period_end):
    """Screen common per-share cash facts, excluding aggregate subsidiary dividends."""
    from datetime import date, timedelta

    recent_from = (date.fromisoformat(period_end) - timedelta(days=183)).isoformat()
    if statistics["last_adjustment"] and statistics["last_adjustment"] >= recent_from:
        return None

    candidates = []
    for tag in (
        "CommonStockDividendsPerShareCashPaid",
        "CommonStockDividendsPerShareDeclared",
    ):
        units = body.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {})
        for fact in units.get("USD/shares", []):
            is_recent_period = recent_from <= fact["end"] <= period_end
            was_filed = fact.get("filed", "9999") <= period_end
            is_positive = number(str(fact["val"]), tag) > 0
            if is_recent_period and was_filed and is_positive:
                candidates.append({"tag": tag, **fact})

    if not candidates:
        return None

    evidence = max(candidates, key=lambda fact: (fact["end"], fact["filed"]))
    return {
        "last_adjustment": statistics["last_adjustment"],
        "reason": "Recent common-share dividends reported, but no recent source adjustment; check ex-dates and share basis before correction",
        "sec_evidence": evidence,
    }


def reviewed_price_events():
    """Hand-reviewed material factor changes, by (ticker, date)."""
    document = read_json(REVIEWED_EVENTS)
    if document["version"] != 1:
        raise ValueError("Unsupported reviewed price events version")
    return {(event["ticker"], event["date"]): event for event in document["events"]}


def separate_reviewed_events(material_events, reviewed):
    """Split the screen's events into reviewed and still unreviewed ones.

    A review covers only the exact factor change it saw: if the prepared prices
    change, the event is unreviewed again. Reviewed events the screen no longer
    finds are returned too, because their disappearance is also a data change.
    """
    unreviewed = []
    matched = set()
    for event in material_events:
        key = (event["ticker"], event["date"])
        review = reviewed.get(key)
        if review and review["factor_change_percent"] == event["factor_change_percent"]:
            matched.add(key)
        else:
            unreviewed.append(event)
    missing = [
        {"ticker": ticker, "date": day}
        for ticker, day in sorted(set(reviewed) - matched)
    ]
    return unreviewed, len(matched), missing


def reviewed_coverage_gaps():
    """Hand-reviewed dividend coverage gaps, by ticker."""
    return read_json(REVIEWED_EVENTS).get("coverage_gaps", {})


def separate_reviewed_gaps(gaps, reviewed):
    """Split coverage gaps into reviewed and still unreviewed ones.

    A review covers only the last adjustment date it saw. Reviewed gaps the screen
    no longer finds are returned too, because that is also a data change.
    """
    unreviewed = {}
    matched = set()
    for ticker, gap in gaps.items():
        review = reviewed.get(ticker)
        if review and review["last_adjustment"] == gap["last_adjustment"]:
            matched.add(ticker)
        else:
            unreviewed[ticker] = gap
    missing = sorted(set(reviewed) - matched)
    return unreviewed, missing


def audit_prices(fetch=False):
    """Screen all 200 prepared histories against cached SEC dividends and a second source."""
    import csv

    from app.eps_splits import split_rules

    from .dividend_audit import audit_dividends, reported_dividends
    from .prices import validate_prices
    from .sec_catalogue import identifiers, load_all_sec
    from .sec_history import HISTORY_INDEX, read_predecessor

    path, _, summary = validate_prices()
    with path.open(encoding="utf-8", newline="") as handle:
        companies, material_events = scan_adjustment_factors(csv.DictReader(handle))

    issuers = identifiers()
    history_index = read_json(HISTORY_INDEX)
    rules = split_rules()
    _, entries = load_all_sec()
    gaps = {}
    reported = {}
    for ticker, (entry, body) in entries:
        gap = find_dividend_coverage_gap(companies[ticker], body, summary["period_end"])
        if gap:
            gaps[ticker] = {**gap, "source": entry["facts"]}
        history = issuers[ticker].get("history") or {}
        predecessors = [
            read_predecessor(source, history_index)
            for source in history.get("predecessors", [])
            # A source outside the current view paid another share's dividends
            # (Johnson Controls before Tyco's shares became JCI's).
            if source.get("current_view", True)
        ]
        reported[ticker] = reported_dividends(ticker, [body, *predecessors], rules)

    dividends = audit_dividends(path, reported, rules, fetch=fetch)
    unreviewed, reviewed_count, missing = separate_reviewed_events(
        material_events, reviewed_price_events()
    )
    unreviewed_gaps, missing_gaps = separate_reviewed_gaps(
        gaps, reviewed_coverage_gaps()
    )
    needs_review = (
        unreviewed_gaps
        or missing_gaps
        or unreviewed
        or missing
        or dividends["status"] == "needs_review"
    )
    return {
        "status": "needs_review" if needs_review else "screen_passed",
        "companies": len(companies),
        "prices": summary["prices"],
        "adjustment_rules_sha256": summary["adjustment_rules_sha256"],
        "dividend_coverage_candidates": unreviewed_gaps,
        "reviewed_coverage_gaps_not_found": missing_gaps,
        "dividend_audit": dividends,
        "material_factor_changes": unreviewed,
        "reviewed_factor_changes": reviewed_count,
        "reviewed_events_not_found": missing,
        "limitations": [
            "A screen is not an independent verification of every corporate action",
            "Material changes can be valid special dividends or spin-offs; no automatic correction is inferred",
            "Per-share SEC facts do not provide complete ex-date, cash-distribution or spin-off coverage",
        ],
    }
