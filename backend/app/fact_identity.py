"""Identify exact SEC facts and their reporting issuer, shared by every metric policy."""

import hashlib
import json

from app.fiscal_periods import canonical_period
from app.schemas.stock import FilingValue


def fact_identity(value):
    """Use source dates and exact decimals, independently of JSON number formatting."""
    fact = FilingValue.model_validate(value)
    record = fact.model_dump(mode="json", exclude_none=True)
    number = format(fact.value, "f")
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    record["value"] = "0" if fact.value == 0 else number
    return record


def variants_fingerprint(variants):
    records = [fact_identity(variant) for variant in variants]
    records.sort(key=lambda record: json.dumps(record, sort_keys=True))
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def period_issuer(facts, start, end, calendar):
    ciks = {
        fact.get("source_cik")
        for fact in facts
        if fact.get("history_eligible", True)
        and fact["period_kind"] == "annual"
        and canonical_period(fact, calendar) == (start, end)
    }
    return next(iter(ciks)) if len(ciks) == 1 else None
