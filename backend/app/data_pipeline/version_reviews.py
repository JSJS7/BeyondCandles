"""Check earlier annual-report versions against their own statement tables (stage 1b).

Rule C (app/metric_history.py) confirms an earlier version of a cell when its facts
are identical to today's approved sources, or differ only by rounding or a split.
Every other 10-K version is reviewed here with the metric's existing statement
reader, applied to that filing as if its filing date were today: the value must
appear on the filing's own face statement within displayed rounding and match the
facts of the same filing. Approvals are pinned in data/version-reviews.json with
their source facts; a later change of those facts invalidates the approval.
"""

import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from app.annual_financials import ANNUAL_REPORT_FORMS, annual_periods
from app.data_pipeline import equity as equity_review
from app.data_pipeline import operating_cash_flow as cash_flow_review
from app.data_pipeline.eps import CLASS_MEMBERS, eps_position, instance_eps
from app.data_pipeline.eps import EVIDENCE_INDEX as EPS_EVIDENCE
from app.data_pipeline.net_income import (
    ISSUER_SUPPORTING_TAGS,
    SUPPORTING_TAGS,
    issuer_income_files,
    position,
    verify_exception,
)
from app.data_pipeline.revenue import (
    EVIDENCE_INDEX as INCOME_EVIDENCE,
)
from app.data_pipeline.revenue import download_evidence, verify_filing_period
from app.data_pipeline.revenue_facts import cache_instance
from app.data_pipeline.sec import CacheIntegrityError, SecAccessDenied, extract_facts
from app.data_pipeline.sec_catalogue import identifiers, load_all_sec, verify_reference
from app.data_pipeline.sec_history import (
    HISTORY_INDEX,
    history_eligible,
    prepare_company_facts,
)
from app.data_pipeline.sec_statements import matches_rendered_value
from app.data_pipeline.source_files import (
    ROOT,
    SELECTION,
    read_json,
    sha256,
    write_json,
)
from app.eps import DEFINITION as EPS_DEFINITION
from app.eps_splits import split_rules
from app.equity import DEFINITION as EQUITY_DEFINITION
from app.fact_identity import fact_identity, period_issuer
from app.fiscal_periods import canonical_period, fiscal_rules
from app.metric_history import VERSION_REVIEWS_PATH, company_versions, review_key
from app.net_income import DEFINITION as NET_INCOME_DEFINITION
from app.net_income import PARENT_TAG, calculated_value
from app.operating_cash_flow import DEFINITION as CASH_FLOW_DEFINITION
from app.revenue import period_key
from app.schemas.stock import FilingValue

REPORT_PATH = ROOT / "data/processed/version-reviews-report.json"
METHOD = (
    "Each annual-report version that rule C cannot confirm is checked with the "
    "metric's existing face-statement review, applied to that filing as if its "
    "filing date were today. The value must match the filing's own statement "
    "column within displayed rounding and the facts of the same filing; the "
    "accession, source facts and statement file are pinned."
)


class VersionTask:
    """One unchecked version: a cell as its filing presented it on the filing date."""

    def __init__(self, ticker, facts, version, calendar):
        self.ticker = ticker
        self.metric = version.metric
        self.start = version.period_start
        self.end = version.period_end
        self.accession = version.accession
        self.filed = version.known_from
        self.form = version.form
        self.calendar = calendar
        # Facts known on the filing date: later filings must not influence the review.
        self.known_facts = [fact for fact in facts if fact["filed"] <= self.filed]
        self.filing_facts = [
            fact
            for fact in self.known_facts
            if fact["accession"] == self.accession and fact["history_eligible"]
        ]
        cell_facts = [
            fact
            for fact in self.filing_facts
            if fact["metric"] == self.metric and self.in_period(fact)
        ]
        ciks = {fact["source_cik"] for fact in cell_facts}
        if not ciks:
            # The cell is not tagged in this filing; its other annual facts name the issuer.
            ciks = {
                fact["source_cik"]
                for fact in self.filing_facts
                if fact["period_kind"] == "annual"
                and canonical_period(fact, calendar) == (self.start, self.end)
            }
        self.cik = next(iter(ciks)) if len(ciks) == 1 else None
        self.cell_facts = cell_facts
        # Net-income allocations of this filing that the import did not store.
        self.supporting_facts = []

    def in_period(self, fact):
        start, end = canonical_period(fact, self.calendar)
        if self.metric == "equity":
            return end == self.end
        return (start, end) == (self.start, self.end)

    @property
    def statement_ends(self):
        """Column dates to try: the canonical end first, then the filing's own end.

        A filing may tag a 52/53-week year with its nominal month end (Deere's FY2015
        and FY2016 reports use Oct. 31 for years ending Nov. 1 and Oct. 30). The exact
        period is mapped to the canonical one only by a reviewed alias in
        data/fiscal-periods.json, and the filing's statements use the same date.
        """
        own_ends = {
            fact["period_end"]
            for fact in self.cell_facts
            if fact["period_end"] != self.end
        }
        return [self.end, *sorted(own_ends)]

    @property
    def key(self):
        return review_key(
            self.ticker, self.metric, self.start, self.end, self.accession
        )

    @property
    def source(self):
        return f"{self.cik}:{self.accession}"


def pending_tasks():
    """List every annual-report version that rule C alone leaves unchecked."""
    issuers = identifiers()
    history = read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    calendar = fiscal_rules()
    split_policy = split_rules()
    tasks = []
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        facts = prepare_company_facts(
            entry, body, issuers[ticker].get("history"), history
        )
        versions = company_versions(
            ticker,
            facts,
            split_policy,
            calendar,
            reviews={},
            # Every unchecked report date is a task, not only the first of a run.
            keep_repeats=True,
        )
        company_tasks = [
            VersionTask(ticker, facts, version, calendar)
            for version in versions
            if version.value.status == "needs_review"
            and version.form in ANNUAL_REPORT_FORMS
        ]
        allocation_tasks = [
            task
            for task in company_tasks
            if task.metric == "net_income" and task.cik == entry["cik"]
        ]
        if allocation_tasks:
            history_rules = issuers[ticker].get("history")
            add_supporting_facts(allocation_tasks, entry["cik"], body, history_rules)
        tasks.extend(company_tasks)
    return tasks, issuers, history


def add_supporting_facts(tasks, cik, body, history_rules):
    """Attach the filing's own net-income allocations that the import did not store.

    The import keeps noncontrolling, common-shareholder and preferred positions only
    for periods whose latest report lacks a parent-income row
    (net_income.supporting_facts). An earlier report of such a period may still
    present only group income and its allocations; the same SEC facts of that filing
    are read here and later checked against its statement.
    """
    tags = SUPPORTING_TAGS + ISSUER_SUPPORTING_TAGS.get(cik, ())
    extracted, _ = extract_facts(body, metrics={"net_income": tags})
    for task in tasks:
        stored = [fact_identity(fact) for fact in task.cell_facts]
        added = []
        for fact in extracted:
            if fact["accession"] != task.accession or fact["period_kind"] != "annual":
                continue
            if canonical_period(fact, task.calendar) != (task.start, task.end):
                continue
            fact = dict(fact, source_cik=cik)
            fact["history_eligible"] = history_eligible(fact, cik, history_rules)
            identity = fact_identity(fact)
            if not fact["history_eligible"] or identity in stored:
                continue
            if identity not in [fact_identity(item) for item in added]:
                added.append(fact)
        task.supporting_facts = added


def single_source(task, fact):
    return {"fact": fact_identity(fact), "coefficient": 1}


def income_statements(task, evidence):
    """The cached face income statements of the version's filing, as (reference, html)."""
    entry = evidence.get(task.source, {})
    if "summary" not in entry:
        raise ValueError("The filing is not cached; rerun with --fetch")
    summary = verify_reference(entry["summary"]).read_bytes()
    statements = []
    for filename in issuer_income_files(summary, task.cik):
        if filename not in entry:
            raise ValueError("The income statement is not cached; rerun with --fetch")
        reference = entry[filename]
        html = verify_reference(reference).read_text(encoding="utf-8")
        statements.append((reference, html))
    if not statements:
        raise ValueError("The filing has no recognized face income statement")
    return entry["summary"], statements


def same_value(choices):
    """As in review-equity and review-operating-cash-flow: statements must agree on the value."""
    if len({Decimal(choice["value"]) for choice in choices}) != 1:
        raise ValueError("The filing's statements show different values")
    return choices


def unique_choice(choices):
    """As in review-net-income: statements must agree on one value and sources."""
    identities = {
        (
            choice["value"],
            tuple(
                (component["fact"]["tag"], component["coefficient"])
                for component in choice["components"]
            ),
        )
        for choice in choices
    }
    if len(identities) != 1:
        raise ValueError("No unique verified statement position in this filing")
    return choices[0]


def review_revenue(task, evidence, end):
    """The same per-filing check as review-revenue for the latest filing."""
    entry = evidence.get(task.source, {})
    if "summary" not in entry:
        raise ValueError("The filing is not cached; rerun with --fetch")
    period = period_key(task.cik, task.start, task.end)
    rule, attempts = verify_filing_period(
        task.ticker,
        period,
        task.filing_facts,
        (task.cik, task.accession),
        entry,
        {"filings": {}},
        False,
        evidence,
        statement_end=end,
    )
    if rule is None:
        reasons = sorted({attempt["reason"] for attempt in attempts})
        raise ValueError("; ".join(reasons) or "No verified total revenue")
    tags = rule.get("components") or [rule["tag"]]
    components = []
    for tag in tags:
        matching = [
            fact
            for fact in task.cell_facts
            if fact["taxonomy"] == rule["taxonomy"]
            and fact["tag"] == tag
            and fact["unit"] == "USD"
        ]
        if len({fact["value"] for fact in matching}) != 1:
            raise ValueError(f"No unique {tag} fact in the filing")
        components.append(single_source(task, matching[0]))
    calculation = None
    note = rule.get(
        "note",
        f"Reported total: {rule['label']}. Other candidates are retained "
        "separately; values are not summed.",
    )
    if rule.get("components"):
        calculation = "Net interest income + noninterest income"
        note = (
            "Calculated GAAP revenue from two complementary consolidated "
            "income-statement positions in the same filing."
        )
    return {
        "value": rule["value"],
        "components": components,
        "selected_tag": None if rule.get("components") else rule["tag"],
        "calculation": calculation,
        "note": note,
        "summary": rule["summary"],
        "statement": rule["evidence"],
    }


def review_net_income(task, evidence, end):
    """Match the parent row, or reconcile it exactly as review-net-income does.

    An allocation the import did not store is pinned like a statement position: the
    reconciliation has matched it to its row in this filing's statement.
    """
    current = [
        FilingValue.model_validate(dict(fact))
        for fact in task.cell_facts + task.supporting_facts
        if fact["period_kind"] == "annual"
    ]
    unstored = [fact_identity(fact) for fact in task.supporting_facts]
    parent = [
        value
        for value in current
        if value.taxonomy == "us-gaap"
        and value.tag == PARENT_TAG
        and value.unit == "USD"
    ]
    summary, statements = income_statements(task, evidence)
    choices = []
    issues = []
    for reference, html in statements:
        try:
            if len(parent) == 1:
                # Raises unless the row shows this value in the annual column.
                position(html, PARENT_TAG, end, parent[0].value)
                selected, coefficients, calculation, note = parent, [1], None, None
            else:
                selected, coefficients, calculation, note = verify_exception(
                    html, current, end
                )
        except ValueError as exc:
            issues.append(str(exc))
            continue
        value = calculated_value(
            [
                (source.value, Decimal(coefficient))
                for source, coefficient in zip(selected, coefficients, strict=True)
            ]
        )
        components = []
        for source, coefficient in zip(selected, coefficients, strict=True):
            component = {"fact": fact_identity(source), "coefficient": coefficient}
            if component["fact"] in unstored:
                component["statement_position"] = True
            components.append(component)
        choices.append(
            {
                "value": format(value, "f"),
                "components": components,
                "selected_tag": selected[0].tag if len(selected) == 1 else None,
                "calculation": calculation,
                "note": NET_INCOME_DEFINITION + (" " + note if note else ""),
                "summary": summary,
                "statement": reference,
            }
        )
    if not choices:
        raise ValueError("; ".join(sorted(set(issues))))
    return unique_choice(choices)


def needs_eps_instance(task):
    """As in review-eps: share classes and untagged EPS are read from the original XBRL."""
    return task.metric == "eps_diluted" and (
        task.cik in CLASS_MEMBERS or not task.cell_facts
    )


def instance_candidates(task, entry):
    """EPS facts of the filing's original instance, for the approved share class."""
    if "instance" not in entry:
        raise ValueError("The original XBRL instance is not cached; rerun with --fetch")
    form_fact = task.filing_facts[0]
    metadata = {
        "accession": task.accession,
        "filed": task.filed,
        "form": form_fact["form"],
        "fiscal_period": form_fact.get("fiscal_period"),
    }
    facts = instance_eps(
        verify_reference(entry["instance"]).read_bytes(), task.cik, metadata
    )
    return [
        fact
        for fact in facts
        if canonical_period(fact, task.calendar) == (task.start, task.end)
    ]


def review_eps(task, evidence, end):
    """Find the net diluted EPS row of the income statement and its exact fact.

    An EPS fact missing from the stored facts (or class-specific) is read from the
    filing's original instance and pinned like a statement position.
    """
    from_instance = needs_eps_instance(task)
    entry = evidence["eps"].get(task.source, {})
    candidates = instance_candidates(task, entry) if from_instance else task.cell_facts
    summary, statements = income_statements(task, evidence["income"])
    choices = []
    issues = []
    for reference, html in statements:
        try:
            tag, parsed, _, note = eps_position(html, task.cik, end)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        matching = [
            fact
            for fact in candidates
            if fact["taxonomy"] == "us-gaap"
            and fact["tag"] == tag
            and fact["unit"] == "USD/shares"
            and matches_rendered_value(parsed, fact["value"], end)
        ]
        if len({fact["value"] for fact in matching}) != 1:
            issues.append("EPS source fact does not match the annual statement value")
            continue
        component = single_source(task, matching[0])
        if from_instance:
            component["instance_position"] = True
            component["instance"] = entry["instance"]
        choices.append(
            {
                "value": format(matching[0]["value"], "f"),
                "components": [component],
                "selected_tag": tag,
                "calculation": None,
                "eps_basis": parsed["eps_basis"],
                "note": EPS_DEFINITION + " " + note,
                "summary": summary,
                "statement": reference,
            }
        )
    if not choices:
        raise ValueError("; ".join(sorted(set(issues))))
    return unique_choice(choices)


# Reviewed earlier 10-K filings whose cash-flow statement shows only a continuing
# operating subtotal; each was checked in the filing's primary document (29.10.2026).
REVIEWED_CASH_FLOW_SCOPES = {
    # The discontinued line of the year is a dash (an explicit zero) in the primary
    # document; the rendered R file leaves SLB's cell blank.
    ("0000087347", "0001564590-17-000589"): "explicit_zero",  # SLB FY2016
    ("0000087347", "0001564590-18-000821"): "explicit_zero",  # SLB FY2017
    ("0000010795", "0001628280-15-008984"): "explicit_zero",  # BDX FY2015
    ("0000798354", "0000798354-17-000006"): "explicit_zero",  # FI FY2016
    # No discontinued operations anywhere in the filing (UTC before its spin-offs).
    ("0000101829", "0000101829-20-000013"): "no_discontinued",  # RTX FY2019
    # As the latest view decides: only the continuing amount is reported.
    ("0000831001", "0000831001-17-000038"): "not_material",  # C
    ("0000831001", "0000831001-18-000040"): "not_material",
    ("0000831001", "0000831001-19-000027"): "not_material",
    ("0000831001", "0000831001-20-000031"): "not_material",
    ("0000831001", "0000831001-21-000042"): "not_material",
    ("0000831001", "0000831001-22-000036"): "not_material",
    ("0000040533", "0000040533-16-000056"): "aggregate_only",  # GD
    ("0000040533", "0000040533-17-000006"): "aggregate_only",
    ("0000040533", "0000040533-18-000008"): "aggregate_only",
    ("0000040533", "0000040533-19-000010"): "aggregate_only",
}
REVIEWED_SCOPE_NOTES = {
    "explicit_zero": (
        "total",
        "The filing's primary document shows this year's discontinued cash flow as a "
        "dash, an explicit zero, so the continuing operating subtotal is the "
        "whole-company total. This is a source-specific review, not a tag alias.",
    ),
    "no_discontinued": (
        "total",
        "The filing reports no discontinued operations, so its continuing operating "
        "subtotal is the whole-company total. This is a source-specific review.",
    ),
    "not_material": (
        "continuing",
        "The reported continuing operating cash flow is shown. The filing describes "
        "discontinued cash flows as not material without a separately quantified "
        "operating amount; not material does not mean zero. This continuing-only "
        "value must not be treated as a whole-company total in calculations.",
    ),
    "aggregate_only": (
        "continuing",
        "The reported continuing operating cash flow is shown. The filing reports an "
        "aggregate discontinued cash-flow amount without a separately quantified "
        "operating component. This continuing-only value must not be treated as a "
        "whole-company total in calculations.",
    ),
}


def reviewed_cash_flow_scope(task, positions, has_discontinued):
    """The continuing subtotal of a reviewed filing, with its scope and note.

    Each resolution is checked against the statement again: a separately reported
    discontinued operating amount, or for "no_discontinued" any discontinued row,
    rejects the review.
    """
    resolution = REVIEWED_CASH_FLOW_SCOPES.get((task.cik, task.accession))
    if resolution is None:
        return None
    continuing = positions.get(cash_flow_review.CONTINUING_TAG)
    total = positions.get(cash_flow_review.TOTAL_TAG)
    if continuing is None and total and total["scope"] == "continuing":
        continuing = total
    discontinued = positions.get(cash_flow_review.DISCONTINUED_TAG) or positions.get(
        cash_flow_review.DISCONTINUED_CASH_TAG
    )
    if continuing is None:
        return None
    if discontinued and discontinued["value"] != 0:
        return None
    if resolution == "no_discontinued" and has_discontinued:
        return None
    scope, note = REVIEWED_SCOPE_NOTES[resolution]
    return [continuing], scope, note


def review_cash_flow(task, evidence, end):
    """The same statement reading and component choice as review-operating-cash-flow."""
    entry = evidence.get(task.source, {})
    if "summary" not in entry:
        raise ValueError("The filing is not cached; rerun with --fetch")
    if not task.cell_facts:
        raise ValueError("No stored filing anchor for this annual cash flow")
    filenames = cash_flow_review.cash_flow_files(
        verify_reference(entry["summary"]).read_bytes(), task.cik
    )
    choices = []
    issues = []
    for filename in filenames:
        if filename not in entry:
            issues.append("The cash-flow statement is not cached; rerun with --fetch")
            continue
        reference = entry[filename]
        html = verify_reference(reference).read_text(encoding="utf-8")
        try:
            positions, discontinued = cash_flow_review.cash_positions(html, end)
            scope = "total"
            try:
                selected, calculation, note = cash_flow_review.choose_positions(
                    positions, discontinued
                )
            except ValueError:
                reviewed = reviewed_cash_flow_scope(task, positions, discontinued)
                if reviewed is None:
                    raise
                selected, scope, note = reviewed
                calculation = None
            components = [
                cash_flow_review.source_component(
                    item, task.cell_facts, task.cell_facts[0], reference
                )
                for item in selected
            ]
        except (ValueError, ET.ParseError) as exc:
            issues.append(str(exc))
            continue
        with localcontext() as context:
            context.prec = 50
            value = sum(
                (Decimal(component["fact"]["value"]) for component in components),
                Decimal(0),
            )
        choices.append(
            {
                "value": format(value, "f"),
                "components": [
                    {
                        key: component[key]
                        for key in ("fact", "coefficient", "statement_position")
                        if key in component
                    }
                    for component in components
                ],
                "selected_tag": (
                    components[0]["fact"]["tag"] if len(components) == 1 else None
                ),
                "calculation": calculation,
                "scope": scope,
                "note": CASH_FLOW_DEFINITION + " " + note,
                "summary": entry["summary"],
                "statement": reference,
            }
        )
    if not choices:
        raise ValueError("; ".join(sorted(set(issues))) or "No cash-flow statement")
    return same_value(choices)[0]


def equity_task(task):
    """The equity review task as it would have been built on the filing date."""
    periods = annual_periods(task.known_facts, equity_policy={})
    period = next(
        period
        for period in periods
        if (period.period_start, period.period_end) == (task.start, task.end)
    )
    variants = [
        variant
        for variant in period.metrics["equity"].variants
        if variant.tag not in equity_review.COMPONENT_TAGS
    ]
    return {
        "ticker": task.ticker,
        "primary": period_issuer(task.known_facts, task.start, task.end, task.calendar),
        "period": period,
        "variants": variants,
        "facts": task.known_facts,
    }


def equity_kind(task):
    return "equity" if (task.filed - task.end).days > 450 else "balance"


def review_equity(task, evidence, end):
    """The same per-filing source review as review-equity."""
    filed = task.filed
    choices, issues, _, _ = equity_review.source_choices(
        equity_task(task),
        task.cik,
        task.accession,
        filed,
        evidence.get(task.source, {}),
        task.calendar,
        statement_end=end,
    )
    if not choices:
        raise ValueError("; ".join(sorted(set(issues))) or "No equity position")
    if any(
        component.get("history_fallback")
        for choice in choices
        for component in choice["components"]
    ):
        raise ValueError("A history-fallback source is not supported for versions")
    # The simplest explanation of the same value, as review-equity selects it.
    choice = min(same_value(choices), key=lambda item: len(item["components"]))
    return {
        "value": choice["value"],
        "components": choice["components"],
        "selected_tag": (
            choice["components"][0]["fact"]["tag"]
            if len(choice["components"]) == 1
            else None
        ),
        "calculation": choice["calculation"],
        "note": EQUITY_DEFINITION + " " + choice["note"],
        "summary": choice["summary"],
        "statement": choice["statement"],
    }


REVIEWS = {
    "revenue": review_revenue,
    "net_income": review_net_income,
    "eps_diluted": review_eps,
    "operating_cash_flow": review_cash_flow,
    "equity": review_equity,
}


def review_with_statement_end(task, evidence):
    """Review at the canonical end; only if that fails, at the filing's own end date."""
    issues = []
    for end in task.statement_ends:
        try:
            return REVIEWS[task.metric](task, evidence, end)
        except (SecAccessDenied, CacheIntegrityError):
            raise
        except ValueError as exc:
            issues.append(str(exc))
    raise ValueError("; ".join(dict.fromkeys(issues)))


def evidence_indexes():
    income = read_json(INCOME_EVIDENCE) if INCOME_EVIDENCE.exists() else {}
    cash_flow = (
        read_json(cash_flow_review.EVIDENCE_INDEX)
        if cash_flow_review.EVIDENCE_INDEX.exists()
        else {}
    )
    equity = (
        read_json(equity_review.EVIDENCE_INDEX).get("filings", {})
        if equity_review.EVIDENCE_INDEX.exists()
        else {}
    )
    eps = read_json(EPS_EVIDENCE) if EPS_EVIDENCE.exists() else {}
    return {"income": income, "cash_flow": cash_flow, "equity": equity, "eps": eps}


def fetch_evidence(tasks, issuers, history, indexes):
    """Cache the statements of the reviewed filings with the existing downloaders."""
    income_jobs = set()
    cash_flow_jobs = set()
    equity_jobs = set()
    for task in tasks:
        if task.cik is None:
            continue
        if task.metric in ("revenue", "net_income", "eps_diluted"):
            income_jobs.add((task.cik, task.accession))
        elif task.metric == "operating_cash_flow":
            cash_flow_jobs.add((task.cik, task.accession))
        else:
            equity_jobs.add((task.source, equity_kind(task)))
    if income_jobs:
        download_evidence(issuers, history, indexes["income"], extra_jobs=income_jobs)
    if cash_flow_jobs:
        download_evidence(
            issuers,
            history,
            indexes["cash_flow"],
            extra_jobs=cash_flow_jobs,
            statement_selector=cash_flow_review.evidence_files,
            evidence_path=cash_flow_review.EVIDENCE_INDEX,
        )
    if equity_jobs:
        equity_review.cache_evidence(equity_jobs, indexes["equity"])
    # Instances are large; fetch them one at a time, only where review-eps would.
    for task in tasks:
        if task.cik is None or not needs_eps_instance(task):
            continue
        entry = indexes["eps"].setdefault(task.source, {})
        if "instance" not in entry:
            cache_instance(task.cik, task.accession, entry)
            write_json(EPS_EVIDENCE, indexes["eps"])


def review_versions(fetch=False):
    """Review every unchecked version and publish data/version-reviews.json."""
    tasks, issuers, history = pending_tasks()
    indexes = evidence_indexes()
    if fetch:
        fetch_evidence(tasks, issuers, history, indexes)
    evidence_for = {
        "revenue": indexes["income"],
        "net_income": indexes["income"],
        "eps_diluted": {"income": indexes["income"], "eps": indexes["eps"]},
        "operating_cash_flow": indexes["cash_flow"],
        "equity": indexes["equity"],
    }
    policy = {
        "version": 1,
        "selection_sha256": sha256(SELECTION),
        "method": METHOD,
        "versions": {},
        "unresolved": {},
    }
    counts = Counter()
    for task in tasks:
        try:
            if not task.cell_facts and task.metric != "eps_diluted":
                raise ValueError("The filing has no stored fact for this cell")
            if task.cik is None:
                raise ValueError("The filing has no single issuer for this cell")
            review = review_with_statement_end(task, evidence_for[task.metric])
        except (SecAccessDenied, CacheIntegrityError):
            raise
        except (ValueError, KeyError, ET.ParseError) as exc:
            policy["unresolved"][task.key] = {
                "filed": str(task.filed),
                "reason": str(exc),
            }
            counts[task.metric + ":unresolved"] += 1
            continue
        # The statement file identifies the filing; its index file is not needed.
        review.pop("summary", None)
        policy["versions"][task.key] = {
            "filed": str(task.filed),
            "form": task.form,
            **review,
        }
        counts[task.metric + ":reviewed"] += 1
    policy["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    policy["versions"] = dict(sorted(policy["versions"].items()))
    policy["unresolved"] = dict(sorted(policy["unresolved"].items()))
    write_json(VERSION_REVIEWS_PATH, policy)
    report = {
        "status": "completed_with_gaps" if policy["unresolved"] else "reviewed",
        "versions": len(tasks),
        "reviewed": len(policy["versions"]),
        "unresolved": len(policy["unresolved"]),
        "by_metric": dict(sorted(counts.items())),
        "unresolved_reasons": dict(
            Counter(
                issue["reason"][:160] for issue in policy["unresolved"].values()
            ).most_common(40)
        ),
    }
    write_json(REPORT_PATH, report)
    return report
