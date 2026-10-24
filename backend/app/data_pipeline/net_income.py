"""Review exceptional parent-income definitions against cached SEC income statements."""

import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from app.data_pipeline.sec import extract_facts
from app.data_pipeline.source_files import SELECTION, read_json, sha256
from app.net_income import PARENT_TAG, RULES_PATH, calculated_value

COMMON_TAG = "NetIncomeLossAvailableToCommonStockholdersBasic"
NCI_TAGS = (
    "NetIncomeLossAttributableToNoncontrollingInterest",
    "NetIncomeLossAttributableToRedeemableNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsAttributableToNoncontrollingEntity",
)
PREFERRED_TAGS = (
    "PreferredStockDividendsAndOtherAdjustments",
    "PreferredStockDividendsIncomeStatementImpact",
    "DividendsPreferredStockStock",
)
SUPPORTING_TAGS = NCI_TAGS + (COMMON_TAG,) + PREFERRED_TAGS
ISSUER_SUPPORTING_TAGS = {
    # These are not universal net-income synonyms. PNC's reviewed face statements
    # use these concepts for specifically labelled net-income positions.
    "0000713676": (
        "ComprehensiveIncomeNetOfTaxAttributableToNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "0000831259": (
        "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToNoncontrollingInterest",
    ),
}


def supporting_facts(body, base_facts):
    """Import supporting positions only for existing annual periods needing reconciliation."""
    periods = defaultdict(list)
    for fact in base_facts:
        if fact["period_kind"] == "annual":
            periods[(fact["period_start"], fact["period_end"])].append(fact)
    needed = set()
    for period, group in periods.items():
        income = [f for f in group if f["metric"] == "net_income"]
        parents = [f for f in income if f["tag"] == PARENT_TAG]
        if not parents or max(f["filed"] for f in parents) < max(
            f["filed"] for f in income
        ):
            needed.add(period)
    if not needed:
        return []
    tags = SUPPORTING_TAGS + ISSUER_SUPPORTING_TAGS.get(
        str(body.get("cik", "")).zfill(10), ()
    )
    facts, _ = extract_facts(body, metrics={"net_income": tags})
    return [
        fact
        for fact in facts
        if fact["period_kind"] == "annual"
        and (fact["period_start"], fact["period_end"]) in needed
    ]


def position(html, tag, end, expected=None, allocation=False):
    """Match source value, date and scale even in joint statements with spanned footnotes."""
    from html import escape

    from app.data_pipeline.sec_statements import (
        StatementRows,
        matches_rendered_value,
        statement_total,
    )

    parser = StatementRows()
    parser.feed(html)
    headers = []
    matches = []
    for row in parser.rows:
        if row["header"]:
            # Keep unlabelled columns aligned, but never classify them as annual.
            if any("months ended" in cell.lower() for cell in row["cells"]):
                row["cells"] = [cell or "Unknown months ended" for cell in row["cells"]]
            headers.append(
                "<tr>"
                + "".join(
                    f'<th colspan="{span}">{escape(cell)}</th>'
                    for cell, span in zip(row["cells"], row["spans"])
                )
                + "</tr>"
            )
            continue
        if any(name.startswith("EarningsPerShare") for _, name in row["tags"]):
            break
        if row["tags"] != {("us-gaap", tag)}:
            continue
        expanded = []
        for cell, span in zip(row["cells"][1:], row["spans"][1:]):
            expanded.extend([cell] + [""] * (span - 1))
        body = f'<tr><td><a onclick="defref_us-gaap_{tag}">{escape(row["cells"][0])}</a></td>'
        body += "".join("<td>" + escape(cell) + "</td>" for cell in expanded) + "</tr>"
        table = '<table class="report">' + "".join(headers) + body + "</table>"
        try:
            parsed = statement_total(table, end, concept=tag, stop_at_costs=False)
        except ValueError:
            continue
        if expected is not None:
            correct = matches_rendered_value(parsed, expected, end)
            if allocation:
                correct = correct or matches_rendered_value(parsed, -expected, end)
            if not correct:
                continue
        for column_end, annual, cell in zip(
            parsed["dates"], parsed["annual_columns"], parsed["values"]
        ):
            if column_end != end or not annual:
                continue
            text = re.sub(r"[$,\s]", "", cell).replace("(", "-").replace(")", "")
            try:
                shown = Decimal(text) * parsed["scale"]
            except InvalidOperation:
                continue
            matches.append((parsed, shown))
    if not matches or len({shown for _, shown in matches}) != 1:
        raise ValueError(f"No unique source-matching annual {tag} position")
    return matches[0]


def verify_exception(html, current, end, parent_control=False):
    """Reconcile parent income without treating a missing allocation as numeric zero."""
    from app.data_pipeline.sec_statements import (
        StatementRows,
        matches_rendered_value,
        normalized_label,
    )

    parser = StatementRows()
    parser.feed(html)
    present = set()
    labels = {}
    for row in parser.rows:
        if any(tag.startswith("EarningsPerShare") for _, tag in row["tags"]):
            break
        if not row["header"] and row["cells"]:
            for taxonomy, tag in row["tags"]:
                if taxonomy == "us-gaap":
                    present.add(tag)
                    labels[tag] = row["cells"][0]
    by_tag = defaultdict(list)
    for fact in current:
        if fact.taxonomy == "us-gaap" and fact.unit == "USD":
            by_tag[fact.tag].append(fact)

    def checked(tag, allocation=False):
        candidates = by_tag[tag]
        if len(candidates) != 1:
            raise ValueError(f"Missing or conflicting fact for {tag}")
        fact = candidates[0]
        row, shown = position(html, tag, end, fact.value, allocation)
        matches = matches_rendered_value(row, fact.value, end)
        # SEC renderers negate deductions for presentation; source fact signs remain intact.
        if not matches and allocation:
            matches = matches_rendered_value(row, -fact.value, end)
        if not matches:
            raise ValueError(f"Source fact does not match the rendered {tag} position")
        return fact

    pnc = bool(current) and current[0].source_cik == "0000713676"
    group_alias = "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest"
    if "ProfitLoss" in present or (pnc and group_alias in present):
        if "ProfitLoss" in present:
            total = checked("ProfitLoss")
        else:
            candidates = by_tag["ProfitLoss"]
            if (
                len(candidates) != 1
                or normalized_label(labels[group_alias]) != "net income loss"
            ):
                raise ValueError("Unverified alias for the total net-income row")
            total = candidates[0]
            position(html, group_alias, end, total.value)
        deductions = [tag for tag in NCI_TAGS if tag in present]
        # A continuing-only allocation cannot silently substitute for the total allocation.
        continuing = (
            "IncomeLossFromContinuingOperationsAttributableToNoncontrollingEntity"
        )
        discontinued = "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToNoncontrollingInterest"
        if continuing in deductions and discontinued in present:
            deductions = [continuing, discontinued]
        elif continuing in deductions:
            if len(deductions) > 1:
                deductions.remove(continuing)
            else:
                total_allocations = by_tag[
                    "NetIncomeLossAttributableToNoncontrollingInterest"
                ]
                verified = checked(continuing, allocation=True)
                if (
                    len(total_allocations) == 1
                    and total_allocations[0].value == verified.value
                ):
                    deductions = [continuing]
                else:
                    common = checked(COMMON_TAG)
                    other_allocations = [
                        tag for tag in PREFERRED_TAGS if tag in present
                    ]
                    if (
                        other_allocations
                        or common.value != total.value - verified.value
                    ):
                        raise ValueError(
                            "Only a continuing-operations allocation is reported"
                        )
                    deductions = [continuing]
        pnc_allocation = (
            "ComprehensiveIncomeNetOfTaxAttributableToNoncontrollingInterest"
        )
        if pnc and not deductions and pnc_allocation in present:
            if (
                normalized_label(labels[pnc_allocation])
                != "less net income attributable to noncontrolling interests"
            ):
                raise ValueError(
                    "PNC allocation is not labelled as net income on the face statement"
                )
            deductions = [pnc_allocation]
        subsidiary_dividends = [
            tag
            for tag in PREFERRED_TAGS
            if tag in present and "subsidiar" in labels[tag].lower()
        ]
        deductions.extend(tag for tag in subsidiary_dividends if tag not in deductions)
        if deductions:
            selected = [total]
            for tag in deductions:
                selected.append(checked(tag, allocation=True))
            coefficients = [1] + [-1] * len(deductions)
            note = (
                "Calculated from one income statement: deduct separately reported noncontrolling income "
                "and subsidiary preferred dividends, before the parent's own preferred-shareholder adjustments."
            )
            if current[0].source_cik == "0000831259" and any(
                fact.value != 0
                for fact in by_tag["PreferredStockDividendsAndOtherAdjustments"]
            ):
                note += (
                    " The separate redemption/preferred adjustment is a per-share adjustment "
                    "and is excluded from this net-income measure; this is not the EPS numerator."
                )
            return (
                selected,
                coefficients,
                "Group net income - allocations to other owners",
                note,
            )
        # Exact statement-specific mappings are permitted only after reviewing the full face.
        other_allocations = [
            tag for tag in NCI_TAGS if any(f.value != 0 for f in by_tag[tag])
        ]
        if other_allocations and not parent_control:
            raise ValueError(
                "Nonzero allocations exist outside the face statement; parent definition needs review"
            )
        if parent_control:
            return (
                [total],
                [1],
                None,
                (
                    "The same filing's statement of changes in equity assigns this entire net-income "
                    "position to Mastercard stockholders. Separately tagged noncontrolling activity "
                    "is not deducted a second time."
                ),
            )
        return (
            [total],
            [1],
            None,
            (
                "The reviewed income statement presents this as its net-income bottom line; "
                "it does not present a separate noncontrolling allocation. This is an exact period mapping, not a general ProfitLoss fallback."
            ),
        )
    if COMMON_TAG in present:
        common = checked(COMMON_TAG)
        label = normalized_label(labels[COMMON_TAG])
        exclusions = (
            "common",
            "preferred",
            "noncontrolling",
            "non controlling",
            "continuing",
        )
        explicit_parent = "attributable to" in label and not any(
            word in label for word in exclusions
        )
        if explicit_parent:
            return (
                [common],
                [1],
                None,
                "The reviewed income statement explicitly labels this position as net income attributable to the parent issuer.",
            )
        allocations = [tag for tag in NCI_TAGS + PREFERRED_TAGS if tag in present]
        if allocations or any(
            f.value != 0 for tag in NCI_TAGS + PREFERRED_TAGS for f in by_tag[tag]
        ):
            raise ValueError(
                "Common-shareholder income cannot substitute for parent income with unresolved allocations"
            )
        label = normalized_label(labels[COMMON_TAG])
        if label not in {
            "net income",
            "net income loss",
            "net earnings",
            "net earnings loss",
        }:
            raise ValueError("No explicit unallocated net-income bottom line")
        return (
            [common],
            [1],
            None,
            (
                "The reviewed statement labels this position as its sole net-income bottom line, "
                "without separate preferred-shareholder or noncontrolling allocations. "
                "The common-shareholder concept is not used as a universal parent-income synonym."
            ),
        )
    raise ValueError("No supported parent-income reconciliation on this statement")


def mastercard_control(entry, period, variants):
    """Verify the issuer-specific ProfitLoss meaning in the dated parent-equity column."""
    from app.data_pipeline.sec_statements import StatementRows

    from .sec_catalogue import verify_reference

    reference = entry.get("parent_income_equity")
    if not reference:
        return False
    parser = StatementRows()
    parser.feed(verify_reference(reference).read_text(encoding="utf-8"))
    parent_column = None
    current_year = None
    retained_column = None
    minority_column = None
    matches = []
    expected = {v.value for v in variants if v.tag == "ProfitLoss"}
    for row in parser.rows:
        cells = row["cells"]
        if not cells:
            continue
        if row["header"]:
            for index, cell in enumerate(cells):
                if (
                    cell.lower().startswith("mastercard")
                    and "equity" in cell.lower()
                    and index > 0
                ):
                    parent_column = index
                if cell == "Retained Earnings":
                    retained_column = index
                if "controlling interests" in cell.lower():
                    minority_column = index
            continue
        balance = re.search(
            r"(?:(?:Beginning|Ending) )?balance at Dec[.]? 31, (\d{4})", cells[0], re.I
        )
        if balance:
            current_year = int(balance.group(1)) + 1
        column = parent_column if parent_column is not None else retained_column
        if current_year != period.period_end.year or column is None:
            continue
        if row["tags"] != {("us-gaap", "ProfitLoss")} or len(cells) <= column:
            continue
        # All reviewed Mastercard equity statements explicitly state USD millions.
        if not any(
            "$ in Millions" in cell
            for header in parser.rows
            if header["header"]
            for cell in header["cells"]
        ):
            raise ValueError("Unverified Mastercard equity currency/scale")
        if minority_column is None or cells[minority_column] not in ("", "0", "$ 0"):
            continue
        text = re.sub(r"[$,\s]", "", cells[column]).replace("(", "-").replace(")", "")
        try:
            matches.append(Decimal(text) * 1000000)
        except InvalidOperation:
            continue
    return len(matches) == 1 and set(matches) == expected


def cache_parent_controls(sources, evidence):
    """Cache only the extra Mastercard statements needed to confirm the parent column."""
    import xml.etree.ElementTree as ET

    from .identifiers import cached
    from .sec_catalogue import verify_reference

    for cik, accession in sorted(sources):
        if cik != "0001141391":
            continue
        entry = evidence[cik + ":" + accession]
        reports = ET.fromstring(verify_reference(entry["summary"]).read_bytes())
        names = [
            r.findtext("HtmlFileName")
            for r in reports.iter("Report")
            if r.findtext("MenuCategory") == "Statements"
            and "changes in equity" in (r.findtext("ShortName") or "").lower()
            and "parenthetical" not in (r.findtext("ShortName") or "").lower()
        ]
        if len(names) != 1 or not re.fullmatch(r"R\d+[.]htm", names[0] or ""):
            raise ValueError("No unique Mastercard equity statement")
        url = entry["summary"]["url"].rsplit("/", 1)[0] + "/" + names[0]
        entry["parent_income_equity"] = cached(
            url, entry.get("parent_income_equity"), json_response=False
        )


def issuer_income_files(summary, cik):
    """Do not confuse a joint filer's parent statement with a subsidiary statement."""
    import xml.etree.ElementTree as ET

    from .revenue import income_statement_files

    candidates = income_statement_files(summary)
    names = {
        "0000004904": {"consolidated statements of income"},
        "0001032208": {"consolidated statements of operations"},
        "0000092122": {"consolidated statements of income - southern"},
    }
    if cik not in names:
        return candidates
    reports = list(ET.fromstring(summary).iter("Report"))
    allowed = names[cik]
    if cik == "0000092122" and not any(
        (r.findtext("ShortName") or "").lower() in allowed for r in reports
    ):
        allowed = {"consolidated statements of income"}
    return [
        r.findtext("HtmlFileName")
        for r in reports
        if (r.findtext("ShortName") or "").lower() in allowed
        and r.findtext("HtmlFileName") in candidates
    ]


def review_net_income(fetch=False):
    """Publish reproducible exception rules, retaining unresolved definitions explicitly."""
    from app.annual_financials import annual_periods
    from app.fiscal_periods import canonical_period, fiscal_rules
    from app.schemas.stock import FilingValue

    from .identifiers import write_json
    from .revenue import EVIDENCE_INDEX, download_evidence
    from .sec_catalogue import identifiers, load_all_sec, verify_reference
    from .sec_history import HISTORY_INDEX, prepare_company_facts

    issuers = identifiers()
    history = read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    evidence = read_json(EVIDENCE_INDEX)
    calendar = fiscal_rules()
    tasks = []
    counts = Counter()
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        facts = prepare_company_facts(
            entry, body, issuers[ticker].get("history"), history
        )
        for period in annual_periods(facts):
            cell = period.metrics["net_income"]
            if cell.status == "ready" and cell.selected_tag == PARENT_TAG:
                counts["standard_parent_periods"] += 1
                continue
            variants = cell.variants
            if not variants:
                tasks.append((ticker, period, []))
                continue
            latest = max(v.filed for v in variants)
            current = [
                FilingValue.model_validate(dict(fact))
                for fact in facts
                if fact["history_eligible"]
                and fact["metric"] == "net_income"
                and fact["period_kind"] == "annual"
                and fact["filed"] == latest
                and canonical_period(fact, calendar)
                == (period.period_start, period.period_end)
            ]
            tasks.append((ticker, period, current))
    sources = {
        (v.source_cik, v.accession) for _, _, variants in tasks for v in variants
    }
    if fetch:
        download_evidence(issuers, history, evidence, extra_jobs=sources)
        cache_parent_controls(sources, evidence)
        write_json(EVIDENCE_INDEX, evidence)
    policy = {
        "version": 1,
        "selection_sha256": sha256(SELECTION),
        "periods": {},
        "unresolved": {},
    }
    for ticker, period, variants in tasks:
        if not variants:
            policy["unresolved"][
                f"{ticker}:{period.period_start}:{period.period_end}"
            ] = "No supported imported net-income facts"
            continue
        ciks = {v.source_cik for v in variants}
        key = f"{variants[0].source_cik}:{period.period_start}:{period.period_end}"
        issues = []
        choices = []
        if len(ciks) != 1:
            issues.append("Overlapping issuer histories")
        else:
            for accession in sorted({v.accession for v in variants}):
                current = [v for v in variants if v.accession == accession]
                entry = evidence.get(variants[0].source_cik + ":" + accession)
                if not entry:
                    issues.append("Latest filing is not cached; rerun with --fetch")
                    continue
                summary = verify_reference(entry["summary"])
                for filename in issuer_income_files(
                    summary.read_bytes(), variants[0].source_cik
                ):
                    if filename not in entry:
                        issues.append("Income statement is not cached")
                        continue
                    statement = verify_reference(entry[filename])
                    try:
                        parent_control = current[
                            0
                        ].source_cik == "0001141391" and mastercard_control(
                            entry, period, current
                        )
                        selected, coefficients, calculation, note = verify_exception(
                            statement.read_text(encoding="utf-8"),
                            current,
                            period.period_end,
                            parent_control,
                        )
                        value = calculated_value(
                            [
                                (v.value, Decimal(c))
                                for v, c in zip(selected, coefficients)
                            ]
                        )
                        choices.append(
                            {
                                "filed": str(selected[0].filed),
                                "accession": accession,
                                "components": [
                                    {"tag": v.tag, "coefficient": c}
                                    for v, c in zip(selected, coefficients)
                                ],
                                "value": format(value, "f"),
                                "calculation": calculation,
                                "note": note,
                                "summary": entry["summary"],
                                "statement": entry[filename],
                                **(
                                    {
                                        "parent_equity_statement": entry[
                                            "parent_income_equity"
                                        ]
                                    }
                                    if parent_control
                                    else {}
                                ),
                            }
                        )
                    except ValueError as exc:
                        issues.append(str(exc))
        identities = set()
        for choice in choices:
            components = tuple(
                (item["tag"], item["coefficient"]) for item in choice["components"]
            )
            identities.add((choice["value"], components))
        if len(identities) == 1:
            policy["periods"][key] = max(
                choices, key=lambda choice: choice["accession"]
            )
        else:
            policy["unresolved"][key] = {
                "ticker": ticker,
                "reasons": sorted(set(issues)) or ["Conflicting parent definitions"],
            }
    write_json(RULES_PATH, policy)
    counts["verified_exceptions"] = len(policy["periods"])
    counts["unresolved"] = len(policy["unresolved"])
    status = "completed_with_gaps" if policy["unresolved"] else "reviewed"
    return {"status": status, **counts, "issues": policy["unresolved"]}
