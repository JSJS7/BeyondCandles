"""Select reported net EPS, retaining its basis, exact source and share-class approvals."""

import json
from functools import lru_cache
from pathlib import Path

from app.equity import fact_identity, variants_fingerprint
from app.schemas.stock import AnnualMetric

RULES_PATH = Path(__file__).resolve().parents[2] / "data/eps-rules.json"
DILUTED_TAG = "EarningsPerShareDiluted"
COMBINED_TAG = "EarningsPerShareBasicAndDiluted"
BASIC_TAG = "EarningsPerShareBasic"
CONTINUING_TAG = "IncomeLossFromContinuingOperationsPerDilutedShare"
UNIT_TAG = "NetIncomeLossNetOfTaxPerOutstandingLimitedPartnershipUnitDiluted"
DEFINITION = (
    "Reported net earnings per common share (or historical common unit), in USD; "
    "prefer diluted, with explicitly reviewed basic fallbacks; original values retain the filing share basis."
)


@lru_cache(maxsize=1)
def load_rules(path, modified):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["version"] != 1:
        raise ValueError("Unsupported EPS policy version")
    return document["periods"]


def eps_rules():
    if not RULES_PATH.exists():
        return {}
    return load_rules(RULES_PATH, RULES_PATH.stat().st_mtime_ns)


def select_eps(variants, start, end, rules=None):
    """Alternative concepts and basic fallbacks require exact, source-backed approvals."""
    result = AnnualMetric(
        status="needs_review" if variants else "not_available",
        variants=list(variants),
        selection_note=DEFINITION,
    )
    if not variants:
        return result
    issuers = {variant.source_cik for variant in variants}
    if (
        None in issuers
        or len(issuers) != 1
        or any(
            variant.unit != "USD/shares" or variant.taxonomy != "us-gaap"
            for variant in variants
        )
    ):
        result.selection_note = DEFINITION + (
            " Conflicting issuer, taxonomy or unit requires review."
        )
        return result
    rules = eps_rules() if rules is None else rules
    key = f"{next(iter(issuers))}:{start}:{end}"
    rule = rules.get(key)
    basis = "diluted"
    if rule:
        if rule["observed_sha256"] != variants_fingerprint(variants):
            result.selection_note = (
                DEFINITION + " EPS sources changed; repeat the source review."
            )
            return result
        matching = [
            variant for variant in variants if fact_identity(variant) == rule["fact"]
        ]
        if len(matching) != 1:
            return result
        if rule["status"] not in ("ready", "not_available"):
            return result
        basis = rule.get("eps_basis", "diluted")
        if basis not in ("basic", "diluted"):
            return result
        if basis == "basic" and (
            matching[0].source_cik != "0001067983"
            or matching[0].tag != BASIC_TAG
            or any(
                variant.tag in (DILUTED_TAG, COMBINED_TAG, UNIT_TAG)
                for variant in variants
            )
        ):
            result.selection_note = DEFINITION + (
                " No approved basic fallback applies to these EPS definitions."
            )
            return result
        result.selection_note = DEFINITION + " " + rule["note"]
        selected = matching[0].model_copy(
            update={
                "statement_url": rule["statement"]["url"],
                "statement_label": rule["label"],
                "statement_column": rule["column"],
            }
        )
        result.variants = [
            selected if variant is matching[0] else variant for variant in variants
        ]
        if rule["status"] == "not_available":
            result.status = "not_available"
            return result
    else:
        current = [variant for variant in variants if variant.tag == DILUTED_TAG]
        if len(current) != 1 or len(variants) != 1:
            result.selection_note = DEFINITION + (
                " No verified diluted net EPS is available for these definitions."
            )
            return result
        selected = current[0]
    result.status = "ready"
    result.value = selected.value
    result.eps_basis = basis
    result.selected_tag = selected.tag
    result.selected_sources = [selected]
    return result
