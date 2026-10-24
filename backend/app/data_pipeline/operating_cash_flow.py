"""Verify annual whole-company operating cash flow against pinned SEC statements."""

import re
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal, InvalidOperation, localcontext
from html import escape
from time import sleep

from app.data_pipeline.identifiers import cached, write_json
from app.data_pipeline.revenue import (
    EVIDENCE_INDEX as INCOME_EVIDENCE,
)
from app.data_pipeline.revenue import download_evidence
from app.data_pipeline.sec import CACHE
from app.data_pipeline.sec_catalogue import identifiers, load_all_sec, verify_reference
from app.data_pipeline.sec_history import HISTORY_INDEX, prepare_company_facts
from app.data_pipeline.sec_statements import (
    StatementRows,
    matches_rendered_value,
    normalized_label,
    statement_total,
)
from app.data_pipeline.source_files import ROOT, SELECTION, read_json, sha256
from app.equity import fact_identity, period_issuer, variants_fingerprint
from app.fiscal_periods import canonical_period, fiscal_rules
from app.operating_cash_flow import (
    CONTINUING_TAG,
    DISCONTINUED_CASH_TAG,
    DISCONTINUED_TAG,
    RULES_PATH,
    TOTAL_TAG,
)

EVIDENCE_INDEX = CACHE / "operating-cash-flow-evidence-index.json"
REPORT_PATH = ROOT / "data/processed/sec-operating-cash-flow-report.json"
EXCEPTIONS_PATH = ROOT / "data/operating-cash-flow-exceptions.json"


def cash_flow_files(summary, cik):
    """Use parent cash-flow face statements, excluding notes, memo tables and subsidiaries."""
    candidates = []
    generic = []
    for report in ET.fromstring(summary).iter("Report"):
        name = (report.findtext("ShortName") or "").lower()
        category = report.findtext("MenuCategory")
        primary = category == "Statements"
        if category in (None, "", "Uncategorized"):
            primary = " - Statement - " in (report.findtext("LongName") or "")
        if not primary or "cash flow" not in name:
            continue
        if any(
            term in name
            for term in (
                "parenthetical",
                "reconciliation",
                "supplemental",
                "noncash",
                "non-cash",
            )
        ):
            continue
        filename = report.findtext("HtmlFileName") or ""
        if not re.fullmatch(r"R\d+[.]htm", filename):
            continue
        if cik == "0000092122" and " - southern" in name:
            return [filename]
        candidates.append(filename)
        if normalized_label(name) in (
            "consolidated statements of cash flows",
            "consolidated statement of cash flows",
            "consolidated statements of cash flow",
        ):
            generic.append(filename)
    return generic or candidates


def evidence_files(summary, cik):
    """Fetch face statements, the Marriott restatement and Citi scope disclosures."""
    files = cash_flow_files(summary, cik)
    if cik == "0001048286":
        for report in ET.fromstring(summary).iter("Report"):
            name = (report.findtext("ShortName") or "").lower()
            filename = report.findtext("HtmlFileName") or ""
            if (
                "effect of adoption of asus on statement of cash flows" in name
                and re.fullmatch(r"R\d+[.]htm", filename)
            ):
                files.append(filename)
    if cik == "0000831001":
        for report in ET.fromstring(summary).iter("Report"):
            name = (report.findtext("ShortName") or "").lower()
            filename = report.findtext("HtmlFileName") or ""
            if (
                report.findtext("MenuCategory") == "Notes"
                and name.startswith("discontinued")
                and re.fullmatch(r"R\d+[.]htm", filename)
            ):
                files.append(filename)
    return files


def row_scope(label):
    """Interpret the reported scope instead of trusting a misleading taxonomy name."""
    label = normalized_label(label)
    if "including" in label and "discontinued" in label:
        return "total"
    if "continuing" in label:
        return "continuing"
    if "discontinued" in label:
        return "discontinued"
    return "total"


def cash_positions(html, end):
    """Read annual operating rows; duplicate identical totals are not competing definitions."""
    parser = StatementRows()
    parser.feed(html)
    headers = []
    positions = {}
    has_discontinued = False
    operating_scope = None
    for row in parser.rows:
        if any(tag.endswith("Axis") for _, tag in row["tags"]):
            # Joint utility reports append subsidiary statements under entity dimensions.
            break
        if row["header"]:
            cells = "".join(
                f'<th colspan="{span}">{escape(cell)}</th>'
                for cell, span in zip(row["cells"], row["spans"])
            )
            headers.append("<tr>" + cells + "</tr>")
            continue
        if not row["cells"]:
            continue
        label = normalized_label(row["cells"][0])
        if (
            row["tags"]
            & {
                ("us-gaap", TOTAL_TAG + "Abstract"),
                ("us-gaap", CONTINUING_TAG + "Abstract"),
            }
            and "operating" in label
        ):
            operating_scope = row_scope(row["cells"][0])
        if "discontinued" in label and ("operating" in label or "cash" in label):
            has_discontinued = True
        for tag in (TOTAL_TAG, CONTINUING_TAG, DISCONTINUED_TAG, DISCONTINUED_CASH_TAG):
            if tag == DISCONTINUED_CASH_TAG and not (
                "operating" in label and "discontinued" in label
            ):
                # Aggregate discontinued cash includes investing/financing unless the row explicitly says otherwise.
                continue
            if row["tags"] != {("us-gaap", tag)}:
                continue
            cells = []
            for cell, span in zip(row["cells"][1:], row["spans"][1:]):
                # A displayed dash is an explicit monetary zero; a blank stays missing.
                if cell.strip() in ("-", "\u2014", "\u2013"):
                    cell = "0"
                cells.append(f'<td colspan="{span}">{escape(cell)}</td>')
            source_row = (
                f'<tr><td><a onclick="defref_us-gaap_{tag}">{escape(row["cells"][0])}</a></td>'
                + "".join(cells)
                + "</tr>"
            )
            table = (
                '<table class="report">' + "".join(headers) + source_row + "</table>"
            )
            try:
                parsed = statement_total(table, end, concept=tag, stop_at_costs=False)
            except ValueError as exc:
                if "has no unique" in str(exc):
                    # A concept may occur only in another comparison year.
                    continue
                raise
            values = []
            for day, annual, cell in zip(
                parsed["dates"], parsed["annual_columns"], parsed["values"]
            ):
                if day != end or not annual:
                    continue
                number = re.sub(r"[$,\s]", "", cell).replace("(", "-").replace(")", "")
                try:
                    value = Decimal(number) * parsed["scale"]
                except InvalidOperation:
                    continue
                if not value.is_finite():
                    raise ValueError("Invalid operating-cash-flow number")
                values.append(value)
            if not values or len(set(values)) != 1:
                raise ValueError(f"No unique annual operating position for {tag}")
            scope = row_scope(row["cells"][0])
            if (
                tag in (TOTAL_TAG, CONTINUING_TAG)
                and scope == "total"
                and operating_scope == "continuing"
            ):
                scope = "continuing"
            if tag in (DISCONTINUED_TAG, DISCONTINUED_CASH_TAG):
                scope = "discontinued"
            position = {
                "tag": tag,
                "value": values[0],
                "label": row["cells"][0],
                "row": parsed,
                "end": end,
                "scope": scope,
            }
            previous = positions.get(tag)
            if previous and (previous["value"], previous["scope"]) != (
                position["value"],
                scope,
            ):
                raise ValueError(
                    "Different cash-flow positions share the same concept and period"
                )
            positions[tag] = position
    return positions, has_discontinued


def choose_positions(positions, has_discontinued):
    """Prefer the explicit total, or add disjoint operating components from one report."""
    total = positions.get(TOTAL_TAG)
    if total and total["scope"] == "total":
        label = normalized_label(total["label"])
        if "operating" not in label and "operations" not in label:
            raise ValueError("The purported operating total has an unrelated label")
        return (
            [total],
            None,
            "Reported whole-company operating cash flow; other definitions remain separate.",
        )
    continuing = positions.get(CONTINUING_TAG)
    if continuing is None and total and total["scope"] == "continuing":
        continuing = total
    if continuing is None:
        raise ValueError("No verified annual operating-cash-flow total")
    discontinued = positions.get(DISCONTINUED_TAG) or positions.get(
        DISCONTINUED_CASH_TAG
    )
    if discontinued:
        return (
            [continuing, discontinued],
            "Continuing operating cash flow + discontinued operating cash flow",
            "Calculated from separately reported operating components in one filing and annual period; original concepts and reported scopes are retained.",
        )
    label = normalized_label(continuing["label"])
    if continuing["scope"] != "total" or has_discontinued:
        raise ValueError(
            "Continuing-only operating cash flow cannot stand for the whole-company total"
        )
    if "operating" not in label and "operations" not in label:
        raise ValueError("No explicit operating-cash-flow bottom line")
    return (
        [continuing],
        None,
        "This exact statement presents an unqualified whole-company operating-cash-flow bottom line using the legacy continuing-operations concept, without separate discontinued operating cash flows. This is a source-specific approval, not a universal tag alias.",
    )


def cash_flow_tasks():
    """Use the frozen raw cache and annual periods, independently of prior cash-flow approvals."""
    from app.annual_financials import annual_periods

    issuers = identifiers()
    history = read_json(HISTORY_INDEX)
    calendar = fiscal_rules()
    tasks = []
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        facts = prepare_company_facts(
            entry, body, issuers[ticker].get("history"), history
        )
        for period in annual_periods(facts, cash_flow_policy={}):
            variants = [
                v
                for v in period.metrics["operating_cash_flow"].variants
                if v.tag in (TOTAL_TAG, CONTINUING_TAG)
            ]
            latest = max((v.filed for v in variants), default=None)
            sources = {
                (v.source_cik, v.accession, v.filed)
                for v in variants
                if v.filed == latest
            }
            tasks.append(
                {
                    "ticker": ticker,
                    "period": period,
                    "facts": facts,
                    "variants": variants,
                    "sources": sources,
                }
            )
    return tasks, calendar, issuers, history


def source_component(position, current, anchor, reference):
    """Retain the stored decimal or an explicitly labelled report position omitted by Companyfacts."""
    candidates = [
        fact
        for fact in current
        if fact["tag"] == position["tag"]
        and fact["taxonomy"] == "us-gaap"
        and fact["unit"] == "USD"
    ]
    matching = [
        fact
        for fact in candidates
        if matches_rendered_value(position["row"], fact["value"], position["end"])
    ]
    if candidates and position["tag"] != DISCONTINUED_CASH_TAG:
        if len(matching) != 1:
            raise ValueError("Cash-flow statement and imported source fact disagree")
        return {
            "fact": fact_identity(matching[0]),
            "coefficient": 1,
            "scope": position["scope"],
        }
    position_fact = dict(
        anchor,
        tag=position["tag"],
        value=position["value"],
        statement_url=reference["url"],
        statement_label=position["label"],
        statement_column=f"Annual period ending {position['end']}",
    )
    return {
        "fact": fact_identity(position_fact),
        "coefficient": 1,
        "statement_position": True,
        "scope": position["scope"],
    }


def reviewed_exceptions(fetch=False):
    """Keep manual scope decisions tied to their exact snapshot and immutable source files."""
    document = read_json(EXCEPTIONS_PATH)
    if document["version"] != 1 or document["selection_sha256"] != sha256(SELECTION):
        raise ValueError(
            "Operating-cash-flow exceptions do not match the frozen selection"
        )
    if fetch:
        references = {}
        for case in document["periods"].values():
            for field in (
                "latest_statement",
                "previous_statement",
                "scope_disclosure",
                "complete_filing",
                "original_instance",
            ):
                reference = case.get(field)
                if reference:
                    references[reference["url"]] = reference
        for reference in references.values():
            try:
                verify_reference(reference)
            except (OSError, ValueError):
                sleep(0.25)
                actual = cached(reference["url"], reference, json_response=False)
                if actual["sha256"] != reference["sha256"]:
                    raise ValueError("A reviewed operating-cash-flow source changed")
    return document["periods"]


def resolve_reviewed_period(task, calendar, reference, positions, current, exceptions):
    """Resolve a pinned missing component; unknown omissions still require review."""
    period = task["period"]
    start, end = period.period_start, period.period_end
    issuer = period_issuer(task["facts"], start, end, calendar)
    case = exceptions.get(f"{issuer}:{start}:{end}")
    if not case or case["observed_sha256"] != variants_fingerprint(task["variants"]):
        return None
    expected = case["latest_statement"]
    if (reference["url"], reference["sha256"]) != (expected["url"], expected["sha256"]):
        return None
    if not current or any(
        fact["accession"] != case["latest_accession"] for fact in current
    ):
        return None
    if DISCONTINUED_TAG in positions or DISCONTINUED_CASH_TAG in positions:
        return None
    continuing = positions.get(CONTINUING_TAG) or positions.get(TOTAL_TAG)
    if not continuing or continuing["scope"] != "continuing":
        return None
    latest_component = source_component(continuing, current, current[0], reference)
    if latest_component.get("statement_position"):
        return None
    latest_fact = latest_component["fact"]
    if latest_fact["filed"] != str(max(v.filed for v in task["variants"])):
        return None

    if case["resolution"] == "continuing_only":
        if not case.get("scope_disclosure") and not (
            case.get("complete_filing") and case.get("original_instance")
        ):
            raise ValueError(
                "A missing whole-company amount needs reviewed disclosure evidence"
            )
        for field in ("scope_disclosure", "complete_filing", "original_instance"):
            proof = case.get(field)
            if proof:
                body = verify_reference(proof).read_text(encoding="utf-8")
                if field == "original_instance":
                    root = ET.fromstring(body)
                    for node in root:
                        name = node.tag.lower()
                        if (
                            "cash" in name
                            and "operating" in name
                            and "discontinued" in name
                            and node.get("contextRef")
                            and node.get(
                                "{http://www.w3.org/2001/XMLSchema-instance}nil"
                            )
                            != "true"
                        ):
                            raise ValueError(
                                "A discontinued operating fact requires a new scope review"
                            )
                if field == "scope_disclosure":
                    parser = StatementRows()
                    parser.feed(body)
                    text = " ".join(
                        cell for row in parser.rows for cell in row["cells"]
                    ).lower()
                    if not re.search(r"cash flows.{0,120}not material", text):
                        raise ValueError(
                            "The reviewed cash-flow scope disclosure changed"
                        )
        return {
            "scope": "continuing",
            "observed_sha256": case["observed_sha256"],
            "components": [latest_component],
            "value": latest_fact["value"],
            "calculation": None,
            "statement": reference,
            "note": case["note"],
            "evidence": {
                field: case[field]
                for field in (
                    "scope_disclosure",
                    "complete_filing",
                    "original_instance",
                )
                if field in case
            },
        }

    if case["resolution"] != "reported_zero":
        raise ValueError("Unsupported operating-cash-flow exception")
    previous_reference = case["previous_statement"]
    previous_html = verify_reference(previous_reference).read_text(encoding="utf-8")
    previous_positions, previous_flag = cash_positions(previous_html, end)
    selected, calculation, note = choose_positions(previous_positions, previous_flag)
    if (
        len(selected) != 2
        or selected[1]["tag"] != DISCONTINUED_TAG
        or selected[1]["value"] != 0
    ):
        raise ValueError("The reviewed discontinued operating zero was not reported")
    previous_facts = [
        fact
        for fact in task["facts"]
        if fact["metric"] == "operating_cash_flow"
        and fact.get("history_eligible", True)
        and fact["source_cik"] == issuer
        and fact["accession"] == case["previous_accession"]
        and fact["period_kind"] == "annual"
        and canonical_period(fact, calendar) == (start, end)
    ]
    if not previous_facts:
        raise ValueError("The reviewed complete filing has no stored operating anchor")
    components = [
        source_component(
            position, previous_facts, previous_facts[0], previous_reference
        )
        for position in selected
    ]
    old_continuing = Decimal(components[0]["fact"]["value"])
    if old_continuing != Decimal(latest_fact["value"]):
        raise ValueError(
            "The later continuing amount changed; the older zero cannot establish the total"
        )
    if components[0]["fact"]["filed"] >= latest_fact["filed"]:
        raise ValueError(
            "The comparative source must precede the latest continuing statement"
        )
    return {
        "observed_sha256": case["observed_sha256"],
        "anchor": fact_identity(previous_facts[0]),
        "components": components,
        "value": format(old_continuing, "f"),
        "calculation": calculation,
        "note": note + " " + case["note"],
        "statement": previous_reference,
        "current_continuing": latest_fact,
        "current_statement": reference,
    }


def review_cash_flow(fetch=False):
    """Publish source-backed operating-cash-flow choices and reproducible coverage gaps."""
    tasks, calendar, issuers, history = cash_flow_tasks()
    exceptions = reviewed_exceptions(fetch)
    evidence = read_json(EVIDENCE_INDEX) if EVIDENCE_INDEX.exists() else {}
    jobs = {(cik, accession) for task in tasks for cik, accession, _ in task["sources"]}
    for previous_path in (INCOME_EVIDENCE, CACHE / "equity-evidence-index.json"):
        if not previous_path.exists():
            continue
        previous = read_json(previous_path)
        previous = previous.get("filings", previous)
        for cik, accession in jobs:
            key = cik + ":" + accession
            evidence[key] = {**previous.get(key, {}), **evidence.get(key, {})}
    if fetch:
        download_evidence(
            issuers,
            history,
            evidence,
            extra_jobs=jobs,
            statement_selector=evidence_files,
            evidence_path=EVIDENCE_INDEX,
        )
    policy = {
        "version": 1,
        "selection_sha256": sha256(SELECTION),
        "definition": "Prefer whole-company operating cash flow; use explicitly reviewed continuing operating cash flow when the whole-company amount is unavailable.",
        "periods": {},
        "unresolved": {},
    }
    report = {"status": "reviewed", "companies": {}, "totals": {}}
    counts = Counter()
    for task in tasks:
        period = task["period"]
        start, end = period.period_start, period.period_end
        issuer = period_issuer(task["facts"], start, end, calendar)
        key = f"{issuer}:{start}:{end}"
        choices, issues = [], []
        for cik, accession, filed in sorted(task["sources"]):
            entry = evidence.get(cik + ":" + accession, {})
            if "summary" not in entry:
                issues.append(
                    "The latest filing evidence is not cached; rerun with --fetch"
                )
                continue
            current = [
                fact
                for fact in task["facts"]
                if fact["metric"] == "operating_cash_flow"
                and fact["history_eligible"]
                and fact["source_cik"] == cik
                and fact["accession"] == accession
                and canonical_period(fact, calendar) == (start, end)
            ]
            if not current:
                issues.append("No stored filing anchor for this annual cash flow")
                continue
            filenames = cash_flow_files(
                verify_reference(entry["summary"]).read_bytes(), cik
            )
            if (
                cik == "0001048286"
                and accession == "0001628280-19-002337"
                and str(end) == "2016-12-31"
            ):
                # This note presents the restated consolidated annual column before old-basis dimensions.
                filenames = ["R51.htm"]
            for filename in filenames:
                if filename not in entry:
                    issues.append(
                        "The cash-flow statement is not cached; rerun with --fetch"
                    )
                    continue
                reference = entry[filename]
                try:
                    positions, discontinued = cash_positions(
                        verify_reference(reference).read_text(encoding="utf-8"), end
                    )
                    resolution = resolve_reviewed_period(
                        task, calendar, reference, positions, current, exceptions
                    )
                    if resolution:
                        choices.append(resolution)
                        continue
                    selected, calculation, note = choose_positions(
                        positions, discontinued
                    )
                    components = [
                        source_component(position, current, current[0], reference)
                        for position in selected
                    ]
                    with localcontext() as context:
                        context.prec = 50
                        value = sum(
                            (Decimal(c["fact"]["value"]) for c in components),
                            Decimal(0),
                        )
                    choices.append(
                        {
                            "observed_sha256": variants_fingerprint(task["variants"]),
                            "anchor": fact_identity(current[0]),
                            "components": components,
                            "value": format(value, "f"),
                            "calculation": calculation,
                            "note": note,
                            "summary": entry["summary"],
                            "statement": reference,
                        }
                    )
                except (ValueError, ET.ParseError) as exc:
                    issues.append(str(exc))
        company = report["companies"].setdefault(
            task["ticker"], {"approved": 0, "unresolved": []}
        )
        if choices and len({Decimal(choice["value"]) for choice in choices}) == 1:
            choice = choices[0]
            policy["periods"][key] = choice
            company["approved"] += 1
            counts["calculated" if choice["calculation"] else "reported"] += 1
            counts[choice.get("scope", "total")] += 1
        else:
            issue = {
                "observed_sha256": variants_fingerprint(task["variants"]),
                "ticker": task["ticker"],
                "fiscal_year": period.fiscal_year,
                "period_start": str(start),
                "period_end": str(end),
                "reasons": sorted(set(issues))
                or ["Missing or conflicting annual operating cash flows"],
            }
            references = {}
            for cik, accession, _ in sorted(task["sources"]):
                entry = evidence.get(cik + ":" + accession, {})
                for name, reference in entry.items():
                    if isinstance(reference, dict) and "url" in reference:
                        references[reference["url"]] = reference
            issue["evidence"] = sorted(
                references.values(), key=lambda item: item["url"]
            )
            if issue["reasons"] == [
                "Continuing-only operating cash flow cannot stand for the whole-company total"
            ]:
                issue["note"] = (
                    "The latest report identifies continuing-only operating cash flow, but a separate discontinued operating component has not been established. Aggregate discontinued cash flow and discontinued net income cannot replace that component. The whole-company amount remains unverified; no zero is assumed."
                )
            else:
                issue["note"] = "Source verification remains incomplete: " + "; ".join(
                    issue["reasons"]
                )
            if task["ticker"] == "C":
                for reference in issue["evidence"]:
                    if not reference["url"].endswith(".htm"):
                        continue
                    parser = StatementRows()
                    parser.feed(verify_reference(reference).read_text(encoding="utf-8"))
                    text = " ".join(
                        cell for row in parser.rows for cell in row["cells"]
                    ).lower()
                    if re.search(r"cash flows.{0,120}not material", text):
                        issue["note"] += (
                            " Citigroup describes discontinued cash flows as not material without disclosing the operating amount."
                        )
                        issue["scope_disclosure"] = reference
                        break
            policy["unresolved"][key] = issue
            company["unresolved"].append(issue)
    report["totals"] = {
        "companies": len(report["companies"]),
        "periods": len(tasks),
        "approved": len(policy["periods"]),
        "unresolved": len(policy["unresolved"]),
        **counts,
    }
    if policy["unresolved"]:
        report["status"] = "completed_with_gaps"
    write_json(EVIDENCE_INDEX, evidence)
    write_json(RULES_PATH, policy)
    write_json(REPORT_PATH, report)
    return {"status": report["status"], **report["totals"]}
