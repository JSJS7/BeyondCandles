"""Apply reviewed corporate-action corrections to the frozen Kaggle snapshot."""

from decimal import Decimal, localcontext

from app.data_pipeline.validation import number

PRICE_QUANTUM = Decimal("1e-18")


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


def audit_prices():
    """Run an offline screen of all 200 prepared histories and cached SEC dividends."""
    import csv

    from .prices import validate_prices
    from .sec_catalogue import load_all_sec

    path, _, summary = validate_prices()
    with path.open(encoding="utf-8", newline="") as handle:
        companies, material_events = scan_adjustment_factors(csv.DictReader(handle))

    _, entries = load_all_sec()
    gaps = {}
    for ticker, (entry, body) in entries:
        gap = find_dividend_coverage_gap(companies[ticker], body, summary["period_end"])
        if gap:
            gaps[ticker] = {**gap, "source": entry["facts"]}

    return {
        "status": "needs_review" if gaps or material_events else "screen_passed",
        "companies": len(companies),
        "prices": summary["prices"],
        "adjustment_rules_sha256": summary["adjustment_rules_sha256"],
        "dividend_coverage_candidates": gaps,
        "material_factor_changes": material_events,
        "limitations": [
            "A screen is not an independent verification of every corporate action",
            "Material changes can be valid special dividends or spin-offs; no automatic correction is inferred",
            "Per-share SEC facts do not provide complete ex-date, cash-distribution or spin-off coverage",
        ],
    }
