"""Select reported total revenue using reviewed issuer/period mappings, never tag order."""

import json
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path

from app.schemas.stock import AnnualMetric, FilingValue

RULES_PATH = Path(__file__).resolve().parents[2] / "data/revenue-rules.json"


def period_key(cik, start, end):
    return f"{cik}:{start}:{end}"


@lru_cache(maxsize=1)
def load_rules(path, modified):
    """Cache a policy version; the modification timestamp invalidates a replaced file."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["version"] not in (1, 2):
        raise ValueError("Unsupported revenue policy version")
    return document["periods"]


def revenue_rules():
    """Read tracked mappings; raw SEC documents are not required by HTTP requests."""
    return load_rules(RULES_PATH, RULES_PATH.stat().st_mtime_ns)


def select_revenue(variants, start, end, rules=None, facts=None):
    """Retain every candidate while selecting only a source-backed total for this period."""
    if not variants:
        return AnnualMetric(status="not_available", variants=[])
    rules = revenue_rules() if rules is None else rules
    ciks = {variant.source_cik for variant in variants}
    note = "Total revenue has not been verified against the income statement for this period."
    if len(ciks) != 1:
        return AnnualMetric(
            status="needs_review",
            variants=variants,
            selection_note="Overlapping issuer histories require review.",
        )
    rule = rules.get(period_key(next(iter(ciks)), start, end))
    latest = max(variants, key=lambda value: (value.filed, value.accession))
    if not rule or (rule.get("filed"), rule.get("accession")) != (
        str(latest.filed),
        latest.accession,
    ):
        return AnnualMetric(
            status="needs_review",
            variants=variants,
            selection_note="Total revenue has not been verified for the latest filing. An older tag approval is not reused.",
        )

    if rule and rule.get("components"):
        return calculated_bank_revenue(variants, start, end, rule, facts or [])
    if rule:
        selected = [
            variant
            for variant in variants
            if variant.taxonomy == rule.get("taxonomy", "us-gaap")
            and variant.tag == rule["tag"]
            and variant.unit == "USD"
        ]
        # annual_periods already retains conflicting same-day reports as separate variants.
        if (
            len(selected) == 1
            and str(selected[0].filed) == rule["filed"]
            and selected[0].accession == rule["accession"]
            and selected[0].value == Decimal(rule["value"])
        ):
            return AnnualMetric(
                status="ready",
                value=selected[0].value,
                variants=variants,
                selected_tag=rule["tag"],
                selected_sources=selected,
                selection_note=rule.get(
                    "note",
                    f"Reported total: {rule['label']}. Other candidates are retained separately; values are not summed.",
                ),
            )
        note = "The verified total-revenue definition is missing or has conflicting filing values."
    return AnnualMetric(status="needs_review", variants=variants, selection_note=note)


def sum_reported_values(facts):
    """Preserve the full NUMERIC(38,10) precision when adding complementary facts."""
    with localcontext() as context:
        context.prec = 50
        return sum((fact["value"] for fact in facts), Decimal(0))


def calculated_bank_revenue(variants, start, end, rule, facts):
    """Add complementary GAAP bank components from the latest coherent filing only."""
    components = set(rule["components"])
    sources = {variant.source_cik for variant in variants}
    candidates = [
        fact
        for fact in facts
        if fact["source_cik"] in sources
        and fact["taxonomy"] == "us-gaap"
        and fact["tag"] in components
        and fact["unit"] == "USD"
        and fact["period_start"] == start
        and fact["period_end"] == end
        and fact["form"] in ("10-K", "10-K/A", "10-Q", "10-Q/A")
    ]
    note = "Both GAAP revenue components must be available in the same latest filing."
    if not candidates:
        return AnnualMetric(
            status="needs_review", variants=variants, selection_note=note
        )
    latest_date = max(fact["filed"] for fact in candidates)
    if str(latest_date) != rule["filed"]:
        return AnnualMetric(
            status="needs_review", variants=variants, selection_note=note
        )
    for tag in components:
        values = {
            fact["value"]
            for fact in candidates
            if fact["filed"] == latest_date and fact["tag"] == tag
        }
        if len(values) > 1:
            return AnnualMetric(
                status="needs_review", variants=variants, selection_note=note
            )
    filings = {}
    for fact in candidates:
        if fact["filed"] == latest_date:
            filings.setdefault(fact["accession"], []).append(fact)
    complete = []
    for group in filings.values():
        if (
            len(group) == len(components)
            and {fact["tag"] for fact in group} == components
        ):
            complete.append(group)
    totals = {sum_reported_values(group) for group in complete}
    if len(totals) != 1:
        return AnnualMetric(
            status="needs_review", variants=variants, selection_note=note
        )
    complete = [
        group for group in complete if group[0]["accession"] == rule["accession"]
    ]
    if len(complete) != 1:
        return AnnualMetric(
            status="needs_review", variants=variants, selection_note=note
        )

    selected = complete[0]
    control_values = rule.get("component_values", {})
    if (
        set(control_values) != components
        or any(
            fact["value"] != Decimal(control_values[fact["tag"]]) for fact in selected
        )
        or sum_reported_values(selected) != Decimal(rule["value"])
    ):
        return AnnualMetric(
            status="needs_review", variants=variants, selection_note=note
        )
    selected_sources = [FilingValue.model_validate(dict(fact)) for fact in selected]
    display_variants = list(variants)
    for source in selected_sources:
        if source not in display_variants:
            display_variants.append(source)
    return AnnualMetric(
        status="ready",
        value=next(iter(totals)),
        variants=display_variants,
        selected_sources=selected_sources,
        calculation="Net interest income + noninterest income",
        selection_note=(
            "Calculated GAAP revenue from two complementary consolidated income-statement "
            "positions in the same filing. Taxable-equivalent and adjusted revenue are not used."
        ),
    )
