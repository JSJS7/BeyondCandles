"""Reviewed spin-off corrections for the pinned Benjaminpo price convention."""

import html
import re
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from app.data_pipeline.source_files import ROOT, read_json, sha256
from app.data_pipeline.validation import number

from .price_adjustments import (
    PRICE_QUANTUM,
    cash_adjustment,
    remove_duplicate_distribution,
)


def evidence_file(review, role):
    """Require the exact archived evidence used to approve a corporate action."""
    matches = [entry for entry in review["evidence_files"] if entry.get("role") == role]
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous corporate-action evidence: {role}")
    entry = matches[0]
    path = ROOT / entry["file"]
    directory = ROOT / "data/raw/benjaminpo-v81/evidence"
    if path.resolve().parent != directory.resolve() or sha256(path) != entry["sha256"]:
        raise ValueError(f"Corporate-action evidence checksum mismatch: {role}")
    return path


def vendor_action(review):
    document = read_json(evidence_file(review, "vendor_actions"))
    results = document["chart"]["result"]
    if len(results) != 1 or results[0]["meta"]["symbol"] != review["ticker"]:
        raise ValueError("Corporate-action evidence belongs to another issuer")
    events = results[0]["events"]
    distributions = []
    splits = []
    for name, output in (("dividends", distributions), ("splits", splits)):
        for event in events.get(name, {}).values():
            day = datetime.fromtimestamp(event["date"], timezone.utc).date().isoformat()
            if day == review["ex_date"]:
                output.append(event)
    if len(distributions) != 1:
        raise ValueError("Expected one vendor distribution at the reviewed ex-date")
    if number(str(distributions[0]["amount"]), "vendor distribution") != Decimal(
        review["original_amount"]
    ):
        raise ValueError("Vendor action amount differs from the reviewed correction")
    return distributions[0], splits


def observed_cash(rows, position):
    previous = rows[position - 1]
    current = rows[position]
    previous_close = number(previous["close"], "prior Close")
    ratio_before = (
        number(previous["adjusted_close"], "prior Adjusted Close") / previous_close
    )
    ratio_after = number(current["adjusted_close"], "Adjusted Close") / number(
        current["close"], "Close"
    )
    return previous_close * (1 - ratio_before / ratio_after)


def evidence_text(review, role):
    body = evidence_file(review, role).read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", body)
    return " ".join(html.unescape(text).split())


def primary_adient_close(review):
    """Read the first regular-way closing price from Adient's own 2016 SEC filing."""
    text = evidence_text(review, "annual_report")
    match = re.search(
        r"The October 31, 2016 closing share price was \$([0-9]+\.[0-9]+)", text
    )
    if match is None:
        raise ValueError("Adient's primary closing-price disclosure was not found")
    close = number(match.group(1), "Adient closing price")
    if close != Decimal(review["distributed_close"]):
        raise ValueError("Adient closing price differs from the reviewed policy")
    return close


def correct_spin_off(rows, review):
    """Change only pre-event Adjusted Close; preserve vendor OHLC and final anchor."""
    if review["status"] != "reviewed" or not review.get("note"):
        raise ValueError("Unapproved spin-off correction")
    positions = {row["date"]: index for index, row in enumerate(rows)}
    position = positions.get(review["ex_date"])
    if position is None or position == 0 or len(positions) != len(rows):
        raise ValueError("Missing or duplicate spin-off sessions")
    _, splits = vendor_action(review)
    changed = 0

    with localcontext() as context:
        context.prec = 50
        amount = Decimal(review["original_amount"])
        if abs(observed_cash(rows, position) - amount) > Decimal("0.0001"):
            raise ValueError(
                "Source prices differ from the reviewed spin-off distribution"
            )

        if review["method"] == "remove_duplicate_distribution":
            # Yahoo records the Fortive spin-off twice: a 1319:1000 conversion
            # already embedded in Close, and an additional 24.56 cash factor.
            # Keep the conversion basis; do not treat the stock distribution as
            # another cash dividend. This is a price-index convention, not a
            # reconstruction of a portfolio holding both DHR and FTV shares.
            if review["ticker"] != "DHR" or len(splits) != 1:
                raise ValueError(
                    "DHR requires the separately recorded vendor conversion"
                )
            split = splits[0]
            conversion = (
                Decimal(str(split["numerator"])),
                Decimal(str(split["denominator"])),
            )
            if conversion != (Decimal("1319"), Decimal("1000")):
                raise ValueError("Unexpected DHR conversion basis")
            if "multiplied by a factor of 1.32" not in evidence_text(
                review, "annual_report"
            ):
                raise ValueError(
                    "Missing primary disclosure of the DHR conversion basis"
                )
            rule = {"ex_date": review["ex_date"], "amount": review["original_amount"]}
            corrected = remove_duplicate_distribution(rows, positions, rule)
        elif review["method"] == "replace_distribution_cash_equivalent":
            # Define a reproducible cash-equivalent value using the first
            # regular-way close, not the vendor's unverified 4.70 estimate.
            if review["ticker"] != "JCI" or splits:
                raise ValueError(
                    "JCI must have one distribution without an additional conversion"
                )
            distributed_close = primary_adient_close(review)
            numerator = number(review["distributed_shares"], "distributed shares")
            denominator = number(review["parent_shares"], "parent shares")
            if (numerator, denominator) != (Decimal(1), Decimal(10)):
                raise ValueError("Unexpected Adient distribution ratio")
            distribution = evidence_text(review, "distribution").lower()
            if (
                "one ordinary share for every ten johnson controls shares"
                not in distribution
            ):
                raise ValueError(
                    "Missing primary disclosure of the Adient distribution ratio"
                )
            equivalent = distributed_close * numerator / denominator
            previous_close = number(rows[position - 1]["close"], "prior Close")
            original_factor = cash_adjustment(previous_close, amount)
            reviewed_factor = cash_adjustment(previous_close, equivalent)
            multiplier = reviewed_factor / original_factor
            corrected = {}
            for row in rows:
                value = number(row["adjusted_close"], "Adjusted Close")
                if row["date"] < review["ex_date"]:
                    value *= multiplier
                corrected[row["date"]] = value.quantize(PRICE_QUANTUM)
        else:
            raise ValueError("Unsupported reviewed spin-off correction")

        for row in rows:
            value = corrected[row["date"]]
            changed += value != Decimal(row["adjusted_close"])
            row["adjusted_close"] = str(value)
    if review["ticker"] == "DHR" and "benchmark" in review:
        verify_dhr_benchmark(rows, review)
    return changed


def verify_dhr_benchmark(rows, review):
    """Reject a correction inconsistent with the issuer's rounded annual graph."""
    evidence_file(review, "annual_performance")
    benchmark = review["benchmark"]
    values = {row["date"]: Decimal(row["adjusted_close"]) for row in rows}
    observed = values[benchmark["end_date"]] / values[benchmark["start_date"]] - 1
    start = Decimal(benchmark["reported_start"])
    end = Decimal(benchmark["reported_end"])
    rounding = Decimal(benchmark["rounding_half_unit"])
    lower = (end - rounding) / (start + rounding) - 1
    upper = (end + rounding) / (start - rounding) - 1
    if not lower <= observed <= upper:
        raise ValueError(
            "DHR correction conflicts with the issuer annual performance graph"
        )
    return observed
