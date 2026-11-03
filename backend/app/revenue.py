"""Select reported total revenue using reviewed issuer/period mappings, never tag order."""

from decimal import Decimal, localcontext
from pathlib import Path

from app.fact_identity import add_selected_sources
from app.fiscal_periods import period_key
from app.policy_files import load_policy
from app.schemas.stock import AnnualMetric, FilingValue

RULES_PATH = Path(__file__).resolve().parents[2] / "data/revenue-rules.json"


def revenue_rules():
    """Read tracked mappings; raw SEC documents are not required by HTTP requests."""
    return load_policy(RULES_PATH, "revenue", versions=(1, 2))["periods"]


def select_revenue(variants, start, end, rules=None, facts=None):
    """Retain every candidate while selecting only a source-backed total for this period."""
    if not variants:
        return AnnualMetric(status="not_available", variants=[])
    rules = revenue_rules() if rules is None else rules
    ciks = {variant.source_cik for variant in variants}
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

    if rule.get("components"):
        return calculated_revenue(variants, start, end, rule, facts or [])
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
    return AnnualMetric(
        status="needs_review",
        variants=variants,
        selection_note="The verified total-revenue definition is missing or has conflicting filing values.",
    )


def sum_reported_values(facts):
    """Preserve the full NUMERIC(38,10) precision when adding complementary facts."""
    with localcontext() as context:
        context.prec = 50
        return sum((fact["value"] for fact in facts), Decimal(0))


def weighted_reported_values(weighted_facts):
    """Add (fact, coefficient) pairs with the full NUMERIC(38,10) precision."""
    with localcontext() as context:
        context.prec = 50
        return sum(
            (
                fact["value"] * Decimal(coefficient)
                for fact, coefficient in weighted_facts
            ),
            Decimal(0),
        )


BANK_CALCULATION = "Net interest income + noninterest income"
BANK_NOTE = (
    "Calculated GAAP revenue from two complementary consolidated income-statement "
    "positions in the same filing. Taxable-equivalent and adjusted revenue are not used."
)


def calculated_revenue(variants, start, end, rule, facts):
    """Combine reviewed components from the latest coherent filing only.

    Banks add net interest income and noninterest income (every coefficient 1, the
    default). A reviewed rule may also deduct a position, e.g. excise taxes.
    """
    components = set(rule["components"])
    coefficients = rule.get("coefficients", {})

    def calculated_total(group):
        return weighted_reported_values(
            [(fact, coefficients.get(fact["tag"], 1)) for fact in group]
        )

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
    totals = {calculated_total(group) for group in complete}
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
        or calculated_total(selected) != Decimal(rule["value"])
    ):
        return AnnualMetric(
            status="needs_review", variants=variants, selection_note=note
        )
    selected_sources = [FilingValue.model_validate(dict(fact)) for fact in selected]
    display_variants = list(variants)
    add_selected_sources(display_variants, selected_sources)
    return AnnualMetric(
        status="ready",
        value=next(iter(totals)),
        variants=display_variants,
        selected_sources=selected_sources,
        calculation=rule.get("calculation", BANK_CALCULATION),
        selection_note=rule.get("note", BANK_NOTE),
    )
