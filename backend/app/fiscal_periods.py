"""Read reviewed fiscal-year labels and exact, source-backed period aliases."""

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[2] / "data/fiscal-periods.json"


def period_key(cik, start, end):
    return f"{cik}:{start}:{end}"


@lru_cache(maxsize=1)
def load_rules(path, modified):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["version"] != 1:
        raise ValueError("Unsupported fiscal-period rules version")
    return document


def fiscal_rules():
    return load_rules(RULES_PATH, RULES_PATH.stat().st_mtime_ns)


def canonical_period(fact, rules):
    """Apply only an exact reviewed alias; nearby dates alone never merge facts."""
    start = fact["period_start"]
    end = fact["period_end"]
    cik = fact.get("source_cik")
    key = period_key(cik, start, end)
    alias = rules.get("aliases", {}).get(key)
    if alias:
        return date.fromisoformat(alias["period_start"]), date.fromisoformat(
            alias["period_end"]
        )
    if fact["period_kind"] == "instant":
        # An explicitly reviewed full-year end-date alias also identifies its balance sheet.
        targets = {
            alias["period_end"]
            for original, alias in rules.get("aliases", {}).items()
            if original.split(":")[0] == cik and original.split(":")[2] == str(end)
        }
        if len(targets) > 1:
            raise ValueError("Conflicting reviewed balance-date aliases")
        if targets:
            canonical_end = date.fromisoformat(next(iter(targets)))
            return canonical_end, canonical_end
    return start, end


def period_metadata(facts, start, end, rules):
    """Require agreement between eligible source issuers; unknown labels stay unknown."""
    years = set()
    notes = set()
    for fact in facts:
        if fact["period_kind"] != "annual" or canonical_period(fact, rules) != (
            start,
            end,
        ):
            continue
        key = period_key(
            fact.get("source_cik"), fact["period_start"], fact["period_end"]
        )
        entry = rules.get("periods", {}).get(key)
        if entry:
            years.add(entry["fiscal_year"])
        alias = rules.get("aliases", {}).get(key)
        if alias:
            notes.add(alias["note"])
    year = next(iter(years)) if len(years) == 1 else None
    note = " ".join(sorted(notes)) or None
    return year, note
