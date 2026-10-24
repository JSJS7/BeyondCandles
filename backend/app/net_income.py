"""Select net income attributable to the parent, preserving other reported definitions."""

import json
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path

from app.schemas.stock import AnnualMetric

RULES_PATH = Path(__file__).resolve().parents[2] / "data/net-income-rules.json"
PARENT_TAG = "NetIncomeLoss"
DEFINITION = "Net income attributable to the parent, before preferred-shareholder and other per-share adjustments."


@lru_cache(maxsize=1)
def load_rules(path, modified):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["version"] != 1:
        raise ValueError("Unsupported net-income policy version")
    return document["periods"]


def net_income_rules():
    return load_rules(RULES_PATH, RULES_PATH.stat().st_mtime_ns)


def calculated_value(components):
    """Keep database precision, including a negative loss attributable to other owners."""
    with localcontext() as context:
        context.prec = 50
        return sum(
            (value * coefficient for value, coefficient in components), Decimal(0)
        )


def select_net_income(variants, start, end, rules=None):
    """Use the parent concept or an exact, source-backed exception for the latest filing."""
    if not variants:
        return AnnualMetric(
            status="not_available", variants=[], selection_note=DEFINITION
        )
    review = AnnualMetric(
        status="needs_review",
        variants=variants,
        selection_note=DEFINITION
        + " No verified parent value is available for the latest filing.",
    )
    if len({v.source_cik for v in variants}) != 1:
        review.selection_note = "Overlapping issuer histories require review."
        return review
    latest_date = max(v.filed for v in variants)
    current = [v for v in variants if v.filed == latest_date]
    parent = [
        variant
        for variant in current
        if variant.taxonomy == "us-gaap"
        and variant.tag == PARENT_TAG
        and variant.unit == "USD"
    ]
    if len(parent) == 1:
        return AnnualMetric(
            status="ready",
            value=parent[0].value,
            variants=variants,
            selected_tag=PARENT_TAG,
            selected_sources=parent,
            selection_note=DEFINITION,
        )
    if parent:
        review.selection_note = (
            "Conflicting latest parent-income values require review."
        )
        return review
    rules = net_income_rules() if rules is None else rules
    key = f"{variants[0].source_cik}:{start}:{end}"
    rule = rules.get(key)
    if not rule or str(latest_date) != rule["filed"]:
        return review
    selected = []
    components = []
    for component in rule["components"]:
        matching = [
            variant
            for variant in current
            if variant.taxonomy == "us-gaap"
            and variant.tag == component["tag"]
            and variant.unit == "USD"
        ]
        if len({v.value for v in matching}) != 1:
            return review
        source = [v for v in matching if v.accession == rule["accession"]]
        if len(source) != 1:
            return review
        selected.append(source[0])
        components.append((source[0].value, Decimal(component["coefficient"])))
    value = calculated_value(components)
    if value != Decimal(rule["value"]):
        return review
    return AnnualMetric(
        status="ready",
        value=value,
        variants=variants,
        selected_sources=selected,
        selected_tag=selected[0].tag if len(selected) == 1 else None,
        calculation=rule.get("calculation"),
        selection_note=DEFINITION + " " + rule["note"],
    )
