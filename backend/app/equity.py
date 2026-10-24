"""Select source-verified equity attributable to the parent's owners."""

import hashlib
import json
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path

from app.fiscal_periods import canonical_period
from app.schemas.stock import AnnualMetric, FilingValue

RULES_PATH = Path(__file__).resolve().parents[2] / "data/equity-rules.json"
DEFINITION = (
    "Equity attributable to the parent's owners, including preferred equity "
    "and excluding noncontrolling interests."
)
HISTORY_FALLBACKS = {
    ("0001744489", "2017-09-30"): "0001001039",
    ("0002012383", "2022-12-31"): "0001364742",
}


@lru_cache(maxsize=1)
def load_rules(path, modified):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["version"] != 1:
        raise ValueError("Unsupported equity policy version")
    return document


def equity_rules():
    if not RULES_PATH.exists():
        return {}
    return load_rules(RULES_PATH, RULES_PATH.stat().st_mtime_ns)["periods"]


def equity_issues():
    if not RULES_PATH.exists():
        return {}
    return load_rules(RULES_PATH, RULES_PATH.stat().st_mtime_ns).get("unresolved", {})


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


def select_equity(variants, start, end, *, facts, calendar, rules=None):
    """Require a reviewed snapshot and retain both rejected candidates and selected sources."""
    issues = equity_issues() if rules is None else {}
    rules = equity_rules() if rules is None else rules
    cik = period_issuer(facts, start, end, calendar)
    rule = rules.get(f"{cik}:{end}")
    status = "needs_review" if variants else "not_available"
    result = AnnualMetric(
        status=status,
        variants=list(variants),
        selection_note=DEFINITION
        + " The reported position has not been verified for this snapshot.",
    )
    if not rule:
        issue = issues.get(f"{cik}:{end}")
        if issue:
            result.selection_note = (
                DEFINITION
                + " "
                + issue.get("note", "The source position still requires review.")
            )
        return result
    if rule["observed_sha256"] != variants_fingerprint(variants):
        result.selection_note = (
            DEFINITION + " The source snapshot changed and requires a new review."
        )
        return result

    selected = []
    terms = []
    for component in rule["components"]:
        expected = component["fact"]
        if component.get("statement_position"):
            anchors = [
                fact for fact in facts if fact_identity(fact) == rule.get("anchor")
            ]
            if len(anchors) != 1:
                return result
            source = dict(anchors[0])
            position = FilingValue.model_validate(expected)
            if (
                position.source_cik != source["source_cik"]
                or position.accession != source["accession"]
                or position.filed != source["filed"]
                or position.period_start != position.period_end
                or position.period_end != source["period_end"]
                or position.form != source["form"]
                or not position.statement_column
                or not position.statement_label
                or position.statement_url != rule["statement"]["url"]
            ):
                return result
            source.update(position.model_dump())
            source["value"] = position.value
            source["period_kind"] = "instant"
        else:
            matches = [
                fact
                for fact in facts
                if fact["metric"] == "equity" and fact_identity(fact) == expected
            ]
            if len(matches) != 1:
                return result
            source = matches[0]
        if not source.get("history_eligible", True):
            allowed = HISTORY_FALLBACKS.get((cik or "", str(end)))
            if not component.get("history_fallback") or source["source_cik"] != allowed:
                return result
        if source["unit"] != "USD" or canonical_period(source, calendar)[1] != end:
            return result
        selected.append(FilingValue.model_validate(source))
        terms.append((source["value"], Decimal(component["coefficient"])))

    if not selected or rule["components"][0]["coefficient"] != 1:
        return result
    if any(component["coefficient"] != -1 for component in rule["components"][1:]):
        return result
    if (
        len(selected) > 1
        and len(
            {
                (fact.source_cik, fact.accession, fact.filed, fact.period_end)
                for fact in selected
            }
        )
        != 1
    ):
        return result
    with localcontext() as context:
        context.prec = 50
        value = sum((amount * coefficient for amount, coefficient in terms), Decimal(0))
    if value != Decimal(rule["value"]):
        return result
    for source in selected:
        if source not in result.variants:
            result.variants.append(source)
    result.status = "ready"
    result.value = value
    result.selected_sources = selected
    result.selected_tag = selected[0].tag if len(selected) == 1 else None
    result.calculation = rule.get("calculation")
    result.selection_note = DEFINITION + " " + rule["note"]
    return result
