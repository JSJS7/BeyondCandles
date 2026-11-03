"""Reports that date earlier versions of a cell but never define today's value.

The issuer-history rules split periods between issuers by the current view: a period the
successor reports again belongs to the successor. On the day an earlier report was filed,
though, the period already was known from the predecessor's own filing. Those filings
enter the point-in-time history only, each confirmed against its own statement table.
A filing missing from SEC companyfacts is handled the same way, from its original XBRL.
"""

from datetime import date
from pathlib import Path

from app.policy_files import load_policy

TIMELINE_RULES_PATH = Path(__file__).resolve().parents[2] / "data/timeline-rules.json"
BOUND_FIELDS = ("filed_from", "filed_to", "period_end_from", "period_end_to")


def timeline_rules():
    return load_policy(TIMELINE_RULES_PATH, "timeline-rules")


def within_bounds(fact, bounds):
    """Evaluate filed_from/to and period_end_from/to; other keys are not bounds."""
    for field, value in bounds.items():
        if field not in BOUND_FIELDS:
            continue
        column, bound = field.rsplit("_", 1)
        boundary = date.fromisoformat(value)
        if bound == "from" and fact[column] < boundary:
            return False
        if bound == "to" and fact[column] > boundary:
            return False
    return True


def is_timeline_filing(fact, rules):
    """Whether the fact comes from a filing read from its original XBRL for dating only."""
    return any(
        fact["accession"] == filing["accession"] and fact["source_cik"] == filing["cik"]
        for filing in rules["filings"]
    )


def on_timeline(fact, rules):
    """Whether a fact outside the current view may date earlier versions of a cell."""
    if fact.get("history_eligible", True):
        return False
    if is_timeline_filing(fact, rules):
        return True
    bounds = rules["issuers"].get(fact["source_cik"])
    return bounds is not None and within_bounds(fact, bounds)


def timeline_facts(facts, rules):
    return [fact for fact in facts if on_timeline(fact, rules)]


def as_known_then(facts, rules):
    """The facts as a reader saw them on their filing date: timeline facts count as usable."""
    result = []
    for fact in facts:
        if on_timeline(fact, rules):
            result.append(dict(fact, history_eligible=True))
        else:
            result.append(fact)
    return result
