"""Group reported annual periods and apply the reviewed financial metric definitions."""

from collections import defaultdict

from app.eps import select_eps
from app.equity import select_equity
from app.fiscal_periods import canonical_period, fiscal_rules, period_metadata
from app.net_income import select_net_income
from app.operating_cash_flow import select_cash_flow
from app.revenue import select_revenue
from app.schemas.stock import AnnualPeriod, FilingValue

METRIC_NAMES = ("revenue", "net_income", "eps_diluted", "equity", "operating_cash_flow")
ANNUAL_REPORT_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
QUARTERLY_REPORT_FORMS = ("10-Q", "10-Q/A")


def annual_periods(facts, period_rules=None, equity_policy=None, cash_flow_policy=None):
    """Keep annual-report periods and choose latest values, including 10-Q revisions."""
    all_facts = list(facts)
    facts = [fact for fact in all_facts if fact.get("history_eligible", True)]
    rules = fiscal_rules() if period_rules is None else period_rules
    # Keep source rows untouched; canonical dates only identify the presentation period.
    dated_facts = [(fact, canonical_period(fact, rules)) for fact in facts]
    # A quarterly filing may revise a full year, but must not define new annual rows.
    periods = sorted(
        {
            dates
            for fact, dates in dated_facts
            if fact["form"] in ANNUAL_REPORT_FORMS and fact["period_kind"] == "annual"
        },
        reverse=True,
    )

    supported_forms = ANNUAL_REPORT_FORMS + QUARTERLY_REPORT_FORMS
    versions = defaultdict(list)
    for fact, dates in dated_facts:
        if fact["form"] not in supported_forms:
            continue
        if fact["period_kind"] not in ("annual", "instant"):
            continue
        key = (
            fact.get("source_cik"),
            fact["metric"],
            fact["taxonomy"],
            fact["tag"],
            fact["unit"],
            dates[0],
            dates[1],
        )
        versions[key].append((fact, dates))

    latest = []
    for group in versions.values():
        latest_date = max(fact["filed"] for fact, _ in group)
        candidates = [
            (fact, dates) for fact, dates in group if fact["filed"] == latest_date
        ]
        if len({fact["value"] for fact, _ in candidates}) == 1:
            latest.append(max(candidates, key=lambda item: item[0]["accession"]))
        else:
            latest.extend(candidates)

    result = []
    for start, end in periods:
        metrics = {}
        for metric in METRIC_NAMES:
            variants = []
            for fact, (fact_start, fact_end) in latest:
                if fact["metric"] != metric or fact_end != end:
                    continue
                if metric != "equity" and fact_start != start:
                    continue
                variants.append(FilingValue.model_validate(dict(fact)))
            variants.sort(
                key=lambda fact: (
                    fact.taxonomy,
                    fact.tag,
                    fact.unit,
                    fact.source_cik or "",
                    fact.accession,
                )
            )
            if metric == "revenue":
                metrics[metric] = select_revenue(variants, start, end, facts=facts)
                continue
            if metric == "net_income":
                metrics[metric] = select_net_income(variants, start, end)
                continue
            if metric == "equity":
                metrics[metric] = select_equity(
                    variants,
                    start,
                    end,
                    facts=all_facts,
                    calendar=rules,
                    rules=equity_policy,
                )
                continue
            if metric == "operating_cash_flow":
                metrics[metric] = select_cash_flow(
                    variants,
                    start,
                    end,
                    facts=all_facts,
                    calendar=rules,
                    rules=cash_flow_policy,
                )
                continue
            metrics[metric] = select_eps(variants, start, end)
        fiscal_year, note = period_metadata(facts, start, end, rules)
        result.append(
            AnnualPeriod(
                fiscal_year=fiscal_year,
                period_note=note,
                period_start=start,
                period_end=end,
                metrics=metrics,
            )
        )
    return result
