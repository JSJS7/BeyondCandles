"""Select verified operating cash flow and retain its economic scope."""

import json
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path

from app.equity import fact_identity, period_issuer, variants_fingerprint
from app.fiscal_periods import canonical_period
from app.schemas.stock import AnnualMetric, FilingValue

RULES_PATH = Path(__file__).resolve().parents[2] / "data/operating-cash-flow-rules.json"
TOTAL_TAG = "NetCashProvidedByUsedInOperatingActivities"
CONTINUING_TAG = "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"
DISCONTINUED_CASH_TAG = "NetCashProvidedByUsedInDiscontinuedOperations"
DISCONTINUED_TAG = "CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations"
DEFINITION = "Net cash from operating activities of the whole company, including discontinued operations."
CONTINUING_DEFINITION = "Net cash from operating activities of continuing operations, excluding discontinued operations."


@lru_cache(maxsize=1)
def load_rules(path, modified):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["version"] != 1:
        raise ValueError("Unsupported operating-cash-flow policy version")
    return document


def cash_flow_rules():
    if not RULES_PATH.exists():
        return {}
    document = load_rules(RULES_PATH, RULES_PATH.stat().st_mtime_ns)
    return {
        **document.get("unresolved", {}),
        **document.get("unavailable", {}),
        **document["periods"],
    }


def select_cash_flow(variants, start, end, *, facts, calendar, rules=None):
    """Require a reviewed snapshot and coherent reported sources; retain all definitions."""
    rules = cash_flow_rules() if rules is None else rules
    issuer = period_issuer(facts, start, end, calendar)
    rule = rules.get(f"{issuer}:{start}:{end}")
    result = AnnualMetric(
        status="needs_review" if variants else "not_available",
        variants=list(variants),
        selection_note=DEFINITION
        + " The reported total has not been verified for this snapshot.",
    )
    if not rule:
        return result
    if rule["observed_sha256"] != variants_fingerprint(variants):
        result.selection_note = (
            DEFINITION + " The source snapshot changed and requires a new review."
        )
        return result
    if "components" not in rule:
        if rule.get("status") == "not_available":
            result.status = "not_available"
        result.selection_note = DEFINITION + " " + rule["note"]
        return result
    scope = rule.get("scope", "total")
    if scope not in ("total", "continuing"):
        return result
    if scope == "continuing" and (
        len(rule["components"]) != 1
        or rule["components"][0].get("scope") != "continuing"
    ):
        return result
    selected = []
    for component in rule["components"]:
        if component["coefficient"] != 1:
            return result
        expected = component["fact"]
        if component.get("statement_position"):
            anchors = [fact for fact in facts if fact_identity(fact) == rule["anchor"]]
            if len(anchors) != 1:
                return result
            anchor = anchors[0]
            position = FilingValue.model_validate(expected)
            if (
                position.source_cik != anchor["source_cik"]
                or position.accession != anchor["accession"]
                or position.filed != anchor["filed"]
                or position.form != anchor["form"]
                or position.period_start != anchor["period_start"]
                or position.period_end != anchor["period_end"]
                or position.statement_url != rule["statement"]["url"]
                or not position.statement_label
                or not position.statement_column
            ):
                return result
            source = dict(anchor, **position.model_dump())
        else:
            matches = [fact for fact in facts if fact_identity(fact) == expected]
            if len(matches) != 1:
                return result
            source = matches[0]
        if (
            source["metric"] != "operating_cash_flow"
            or not source.get("history_eligible", True)
            or source["source_cik"] != issuer
            or source["unit"] != "USD"
            or source["taxonomy"] != "us-gaap"
            or source["period_kind"] != "annual"
            or canonical_period(source, calendar) != (start, end)
            or source["tag"]
            not in (TOTAL_TAG, CONTINUING_TAG, DISCONTINUED_TAG, DISCONTINUED_CASH_TAG)
        ):
            return result
        if source["tag"] == DISCONTINUED_CASH_TAG:
            label = (source.get("statement_label") or "").lower()
            if (
                not component.get("statement_position")
                or "operating" not in label
                or "discontinued" not in label
            ):
                return result
        selected.append(FilingValue.model_validate(source))
    if (
        not selected
        or len(
            {
                (v.source_cik, v.accession, v.filed, v.period_start, v.period_end)
                for v in selected
            }
        )
        != 1
    ):
        return result
    if len(selected) == 1 and selected[0].tag in (
        DISCONTINUED_TAG,
        DISCONTINUED_CASH_TAG,
    ):
        return result
    for source in selected:
        same_tag = [
            v
            for v in variants
            if v.source_cik == source.source_cik
            and v.taxonomy == source.taxonomy
            and v.tag == source.tag
            and v.unit == source.unit
            and v.filed == source.filed
        ]
        if len({v.value for v in same_tag}) > 1:
            return result
    if len(selected) > 1:
        tags = {v.tag for v in selected}
        discontinued = {DISCONTINUED_TAG, DISCONTINUED_CASH_TAG}
        allowed = CONTINUING_TAG in tags and len(tags & discontinued) == 1
        if TOTAL_TAG in tags and len(tags & discontinued) == 1:
            # Some filers use the total concept for an explicitly continuing-only row.
            allowed = rule["components"][0].get("scope") == "continuing"
        if not allowed:
            return result
    if len(selected) > 2:
        return result
    with localcontext() as context:
        context.prec = 50
        value = sum((v.value for v in selected), Decimal(0))
    if value != Decimal(rule["value"]):
        return result
    if variants and selected[0].filed != max(v.filed for v in variants):
        # Four reviewed comparisons preserve an explicit zero in an older complete
        # statement. Never combine amounts from different filing versions.
        comparison = rule.get("current_continuing")
        if not comparison or len(selected) != 2 or selected[1].value != 0:
            return result
        matches = [fact for fact in facts if fact_identity(fact) == comparison]
        if len(matches) != 1:
            return result
        current = matches[0]
        latest_values = {
            variant.value
            for variant in variants
            if variant.source_cik == current["source_cik"]
            and variant.taxonomy == current["taxonomy"]
            and variant.tag == current["tag"]
            and variant.unit == current["unit"]
            and variant.filed == current["filed"]
        }
        if latest_values != {current["value"]}:
            return result
        if (
            current["metric"] != "operating_cash_flow"
            or not current.get("history_eligible", True)
            or current["source_cik"] != issuer
            or current["unit"] != "USD"
            or current["taxonomy"] != "us-gaap"
            or current["tag"] not in (TOTAL_TAG, CONTINUING_TAG)
            or current["period_kind"] != "annual"
            or canonical_period(current, calendar) != (start, end)
            or current["filed"] != max(v.filed for v in variants)
            or current["filed"] <= selected[0].filed
            or current["value"] != selected[0].value
            or selected[1].tag != DISCONTINUED_TAG
        ):
            return result
    for source in selected:
        if source not in result.variants:
            result.variants.append(source)
    result.status = "ready"
    result.value = value
    result.selected_sources = selected
    result.selected_tag = selected[0].tag if len(selected) == 1 else None
    result.calculation = rule.get("calculation")
    result.scope = scope
    definition = CONTINUING_DEFINITION if scope == "continuing" else DEFINITION
    result.selection_note = definition + " " + rule["note"]
    return result
