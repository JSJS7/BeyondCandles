"""Reproduce missing EPS from verified income statements and exact SEC source facts."""

import io
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from decimal import Decimal
from html import escape
from typing import cast

from app.data_pipeline.identifiers import write_json
from app.data_pipeline.revenue_facts import cache_instance
from app.data_pipeline.sec import CACHE, extract_facts, period_kind
from app.data_pipeline.sec_catalogue import verify_reference
from app.data_pipeline.sec_statements import (
    StatementRows,
    matches_rendered_value,
    statement_total,
)
from app.data_pipeline.source_files import SELECTION, read_json, sha256
from app.eps import (
    BASIC_TAG,
    COMBINED_TAG,
    CONTINUING_TAG,
    DILUTED_TAG,
    RULES_PATH,
    UNIT_TAG,
)
from app.equity import fact_identity, variants_fingerprint
from app.fiscal_periods import canonical_period, fiscal_rules

EVIDENCE_INDEX = CACHE / "eps-evidence-index.json"
FACTS_INDEX = CACHE / "eps-facts-index.json"
TAGS = (DILUTED_TAG, COMBINED_TAG, CONTINUING_TAG, UNIT_TAG, BASIC_TAG)
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
# These are identities of the selected security, not a preference for a numeric EPS value.
CLASS_MEMBERS = {
    "0001652044": {
        "us-gaap:CommonClassAMember",
        "goog:CommonClassAandCommonClassBMember",
    },
    "0001403161": {"us-gaap:CommonClassAMember"},
    "0001404912": {
        "us-gaap:CommonStockMember",
        "us-gaap:CommonUnitsMember",
        "us-gaap:CommonClassAMember",
    },
    "0001067983": {"brka:EquivalentClassBMember"},
}
CLASS_LABELS = {
    "0001652044": {"Class A and Class B Common Stock", "Class A Common Stock"},
    "0001403161": {"Class A common stock", "Class A Common Stock"},
    "0001404912": {"Common Stock", "Common Units", "Class A Common Stock"},
    "0001067983": {"Equivalent Class B [Member]"},
}
# Reviewed issuers whose face statements sometimes use a continuing concept for a net EPS row.
# A period is approved only after matching its net-income heading and original numeric fact.
NET_ROW_ALIASES = {"0000006281", "0000004962", "0001163165", "0000831259", "0000865752"}


def instance_eps(body, cik, metadata, *, reviewed_revision=False):
    """Accept USD per share and only the reviewed class; reject other dimensions and entities."""
    namespaces = dict(
        value for _, value in ET.iterparse(io.BytesIO(body), events=("start-ns",))
    )
    root = ET.fromstring(body)
    contexts = {}
    for context in root.findall(XBRLI + "context"):
        identifier = context.findtext(XBRLI + "entity/" + XBRLI + "identifier", "")
        if (
            identifier.strip().zfill(10) != cik
            or context.find(XBRLI + "scenario") is not None
        ):
            continue
        if context.find(".//" + XBRLDI + "typedMember") is not None:
            continue
        members = context.findall(".//" + XBRLDI + "explicitMember")
        # AXON's consolidated revision row has a misleading product dimension.
        # This approval cannot transfer to another issuer, filing, column or period.
        revision_context = (
            reviewed_revision
            and cik == "0001069183"
            and metadata["accession"] == "0001069183-24-000061"
            and metadata["filed"] == date(2024, 11, 8)
            and metadata["form"] == "10-Q"
            and context.get("id") == "c-570"
            and namespaces.get("srt") == "http://fasb.org/srt/2024"
            and len(members) == 1
            and members[0].get("dimension") == "srt:ProductOrServiceAxis"
            and (members[0].text or "").strip() == "us-gaap:ProductMember"
        )
        if members and not revision_context:
            if (
                len(members) != 1
                or members[0].get("dimension") != "us-gaap:StatementClassOfStockAxis"
            ):
                continue
            if (members[0].text or "").strip() not in CLASS_MEMBERS.get(cik, set()):
                continue
        elif cik in CLASS_MEMBERS and not (
            cik == "0001404912"
            and context.findtext(XBRLI + "period/" + XBRLI + "endDate", "9999")
            <= "2018-06-30"
        ):
            # KKR's pre-conversion common-unit total has no stock-class dimension.
            continue
        start = context.findtext(XBRLI + "period/" + XBRLI + "startDate")
        end = context.findtext(XBRLI + "period/" + XBRLI + "endDate")
        if not start or not end:
            continue
        start, end = date.fromisoformat(start), date.fromisoformat(end)
        if revision_context and (start, end) != (date(2021, 1, 1), date(2021, 12, 31)):
            continue
        if (
            date(2015, 1, 1) <= end <= date(2024, 12, 31)
            and period_kind(start, end) == "annual"
        ):
            contexts[context.attrib["id"]] = (start, end)

    units = set()
    for unit in root.findall(XBRLI + "unit"):
        numerator = unit.findall(
            XBRLI + "divide/" + XBRLI + "unitNumerator/" + XBRLI + "measure"
        )
        denominator = unit.findall(
            XBRLI + "divide/" + XBRLI + "unitDenominator/" + XBRLI + "measure"
        )
        if len(numerator) != 1 or len(denominator) != 1:
            continue
        top = (numerator[0].text or "").split(":")
        bottom = (denominator[0].text or "").split(":")
        if len(top) == 1:
            top = ["", top[0]]
        if len(bottom) == 1:
            bottom = ["", bottom[0]]
        if len(top) != 2 or len(bottom) != 2:
            continue
        if (
            top[1] == "USD"
            and namespaces.get(top[0]) == "http://www.xbrl.org/2003/iso4217"
            and bottom[1] == "shares"
            and namespaces.get(bottom[0]) == XBRLI[1:-1]
        ):
            units.add(unit.attrib["id"])

    result = {}
    for element in root:
        match = re.fullmatch(r"\{http://fasb[.]org/us-gaap/[^}]+\}(.+)", element.tag)
        if not match or match.group(1) not in TAGS:
            continue
        context = contexts.get(element.get("contextRef"))
        if (
            context is None
            or element.get("unitRef") not in units
            or element.get("{http://www.w3.org/2001/XMLSchema-instance}nil")
            in ("true", "1")
        ):
            continue
        value = Decimal((element.text or "").strip())
        if (
            not value.is_finite()
            or abs(value) >= Decimal("1e28")
            or cast(int, value.as_tuple().exponent) < -10
        ):
            raise ValueError("Original EPS exceeds the supported numeric range")
        start, end = context
        tag = match.group(1)
        fact = dict(
            metadata,
            source_cik=cik,
            metric="eps_diluted",
            taxonomy="us-gaap",
            tag=tag,
            unit="USD/shares",
            value=value,
            period_start=start,
            period_end=end,
            period_kind="annual",
        )
        key = (tag, start, end)
        if key in result and result[key]["value"] != value:
            raise ValueError("Conflicting EPS for the approved share class")
        result[key] = fact
    return list(result.values())


def eps_position(html, cik, end):
    """Identify net EPS and its basis in the face statement, preserving the annual column."""
    parser = StatementRows()
    parser.feed(html)
    headers = []
    candidates = []
    active = cik not in CLASS_LABELS or cik == "0001404912"
    section = ""
    eps_rows = []
    has_net_income = False
    for row in parser.rows:
        cells = row["cells"]
        if not cells:
            continue
        if row["header"]:
            headers.append(row)
            continue
        axes = [tag for _, tag in row["tags"] if tag.endswith("Axis")]
        if axes:
            if axes != ["StatementClassOfStockAxis"]:
                break
            active = cells[0] in CLASS_LABELS.get(cik, set())
            section = ""
            continue
        if not active:
            continue
        has_net_income |= any(
            tag
            in (
                "NetIncomeLoss",
                "ProfitLoss",
                "NetIncomeLossAvailableToCommonStockholdersBasic",
            )
            for _, tag in row["tags"]
        )
        if any(
            tag.endswith("Abstract") and ("PerShare" in tag or "PerUnit" in tag)
            for _, tag in row["tags"]
        ):
            section = cells[0]
        if any("Discontinued" in tag and ("Per" in tag) for _, tag in row["tags"]):
            eps_rows.append(("Discontinued", cells[0]))
        matching = [
            tag
            for taxonomy, tag in row["tags"]
            if taxonomy == "us-gaap" and tag in TAGS
        ]
        if not matching:
            continue
        tag = matching[0]
        eps_rows.append((tag, cells[0]))
        full_label = (section + " " + cells[0]).lower()
        if (
            "cents" in full_label
            or "discontinued" in full_label
            or "continuing" in section.lower()
        ):
            continue
        if "continuing" in full_label and not (
            cik == "0000831259"
            and end == date(2022, 12, 31)
            and "net income" in section.lower()
        ):
            continue
        if tag == CONTINUING_TAG and cik not in NET_ROW_ALIASES:
            continue
        both = "basic" in full_label and "diluted" in full_label
        if tag == BASIC_TAG and not both and cik != "0001067983":
            continue
        fragments = []
        for source_row in headers + [row]:
            cell_tag = "th" if source_row["header"] else "td"
            rendered = []
            for index, (cell, span) in enumerate(
                zip(source_row["cells"], source_row["spans"])
            ):
                text = escape(cell)
                if source_row is row and index == 0:
                    text = f'<a onclick="defref_us-gaap_{tag}">{text}</a>'
                rendered.append(f'<{cell_tag} colspan="{span}">{text}</{cell_tag}>')
            fragments.append("<tr>" + "".join(rendered) + "</tr>")
        try:
            parsed = statement_total(
                '<table class="report">' + "".join(fragments) + "</table>",
                end,
                concept=tag,
                stop_at_costs=False,
                per_share=True,
            )
        except ValueError:
            continue
        candidates.append((tag, parsed, both))
    # A continuing concept cannot override separately reported discontinued/total EPS.
    if any("Discontinued" in tag for tag, _ in eps_rows):
        candidates = [
            candidate for candidate in candidates if candidate[0] != CONTINUING_TAG
        ]
    if any(candidate[0] in (DILUTED_TAG, COMBINED_TAG) for candidate in candidates):
        candidates = [
            candidate
            for candidate in candidates
            if candidate[0] in (DILUTED_TAG, COMBINED_TAG)
        ]
    if len(candidates) != 1:
        raise ValueError("No unique net diluted EPS row for the selected share class")
    tag, parsed, both = candidates[0]
    if tag == CONTINUING_TAG and not has_net_income:
        raise ValueError(
            "Continuing EPS has no confirmed net-income face-statement context"
        )
    note = "Reported diluted net EPS; the original per-share value is preserved without additional split adjustments."
    status = "ready"
    parsed["eps_basis"] = "diluted"
    if tag == CONTINUING_TAG:
        note += " This issuer uses a continuing-operations concept for its sole diluted net-income row in this reviewed filing."
    if tag in (COMBINED_TAG, BASIC_TAG) and both:
        note += (
            " The statement explicitly identifies the amount as both basic and diluted."
        )
    elif tag == BASIC_TAG:
        parsed["eps_basis"] = "basic"
        note = (
            "Reported basic net EPS for Berkshire Class B, used because verified diluted EPS "
            "is unavailable. This is basic EPS, not a claim of diluted equivalence."
        )
    if cik in CLASS_LABELS:
        note += (
            " The source is restricted to the selected security's approved share class."
        )
    if tag == UNIT_TAG:
        note += " KKR's historical figure is per common partnership unit, before its corporate conversion."
    return tag, parsed, status, note


def json_fact(fact):
    return {
        key: str(value) if isinstance(value, (date, Decimal)) else value
        for key, value in fact.items()
    }


def source_fact(record):
    """Reparse the pinned source; never import a manually copied HTML amount."""
    expected = record["fact"]
    if (
        expected["tag"] not in TAGS
        or expected["metric"] != "eps_diluted"
        or expected["unit"] != "USD/shares"
        or record["source_kind"] not in ("companyfacts", "instance")
    ):
        raise ValueError("Unapproved supplemental EPS definition")
    cik = expected["source_cik"]
    if record["source_kind"] == "companyfacts":
        body = json.loads(
            verify_reference(record["source"]).read_text(encoding="utf-8"),
            parse_float=Decimal,
        )
        if str(body["cik"]).zfill(10) != cik:
            raise ValueError("EPS Companyfacts CIK mismatch")
        facts, _ = extract_facts(body, metrics={"eps_diluted": (expected["tag"],)})
        facts = [dict(fact, source_cik=cik) for fact in facts]
    else:
        metadata = {
            key: expected[key] for key in ("accession", "form", "fiscal_period")
        }
        metadata["filed"] = date.fromisoformat(expected["filed"])
        facts = instance_eps(
            verify_reference(record["source"]).read_bytes(),
            cik,
            metadata,
            reviewed_revision=record.get("reviewed_revision", False),
        )
    matches = [fact for fact in facts if json_fact(fact) == expected]
    if len(matches) != 1:
        raise ValueError("Reviewed EPS fact changed or cannot be reproduced")
    return matches[0]


def supplemental_eps(ciks):
    """Rebuild only reviewed EPS additions offline, retaining their original concepts and dates."""
    if not FACTS_INDEX.exists():
        return []
    index = read_json(FACTS_INDEX)
    if index["selection_sha256"] != sha256(SELECTION):
        raise ValueError("EPS facts use a different company selection")
    return [
        source_fact(record)
        for record in index["periods"].values()
        if record["fact"]["source_cik"] in ciks
    ]


AXON_REVISION_KEY = "0001069183:2021-01-01:2021-12-31"


def review_axon_revision(facts, evidence, fetch=False):
    """Reproduce the revised full-year EPS from its original note and exact XBRL context."""
    from app.annual_financials import annual_periods
    from app.data_pipeline.equity_exceptions import verify_original_position
    from app.data_pipeline.identifiers import cached

    cik, accession = "0001069183", "0001069183-24-000061"
    source_key = cik + ":" + accession
    entry = evidence.setdefault(source_key, {})
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    if fetch:
        entry["summary"] = cached(
            base + "FilingSummary.xml", entry.get("summary"), json_response=False
        )
        entry["revision"] = cached(
            base + "R26.htm", entry.get("revision"), json_response=False
        )
        cache_instance(cik, accession, entry)
    position = {
        "row_label": "net income per common and common equivalent shares diluted",
        "header_terms": [
            "Year Ended December 31, 2021",
            "As Reported",
            "Revision",
            "As Revised",
        ],
        "row_numbers": ["-0.91", "0.06", "-0.85"],
        "value_column": 2,
        "scale": 1,
        "value": "-0.85",
    }
    shown = verify_original_position(
        verify_reference(entry["revision"]).read_text(encoding="utf-8"),
        position,
    )
    metadata = {
        "accession": accession,
        "filed": date(2024, 11, 8),
        "form": "10-Q",
        "fiscal_period": "Q3",
    }
    candidates = instance_eps(
        verify_reference(entry["instance"]).read_bytes(),
        cik,
        metadata,
        reviewed_revision=True,
    )
    matching = [
        fact
        for fact in candidates
        if fact["tag"] == DILUTED_TAG
        and fact["period_start"] == date(2021, 1, 1)
        and fact["period_end"] == date(2021, 12, 31)
    ]
    if len(matching) != 1 or matching[0]["value"] != shown:
        raise ValueError("AXON's revised EPS note and original XBRL disagree")
    fact = matching[0]
    if any(
        old["metric"] == "eps_diluted"
        and old["source_cik"] == cik
        and old["period_start"] == fact["period_start"]
        and old["period_end"] == fact["period_end"]
        and old["filed"] > fact["filed"]
        for old in facts
    ):
        raise ValueError("A newer AXON EPS filing requires a new revision review")
    record = {
        "source_kind": "instance",
        "source": entry["instance"],
        "fact": json_fact(fact),
        "reviewed_revision": True,
    }
    source_fact(record)
    updated = list(facts)
    if not any(
        fact_identity(old) == fact_identity(fact)
        for old in facts
        if old["metric"] == "eps_diluted"
    ):
        updated.append(dict(fact, history_eligible=True))
    period = next(
        p
        for p in annual_periods(updated)
        if p.period_start == fact["period_start"] and p.period_end == fact["period_end"]
    )
    variants = [
        v.model_copy(
            update={
                "statement_url": None,
                "statement_label": None,
                "statement_column": None,
            }
        )
        for v in period.metrics["eps_diluted"].variants
    ]
    rule = {
        "ticker": "AXON",
        "status": "ready",
        "eps_basis": "diluted",
        "observed_sha256": variants_fingerprint(variants),
        "fact": fact_identity(fact),
        "note": "Full-year diluted EPS revised from -0.91 to -0.85 in the 2024 Q3 note. The original revision column and XBRL context are verified; this is consolidated net EPS despite the misleading product dimension.",
        "statement": entry["revision"],
        "summary": entry["summary"],
        "label": "Net income per common and common equivalent shares - Diluted",
        "column": "Year ended 2021-12-31; As Revised; USD per share",
        "source": entry["instance"],
    }
    return rule, record


def review_eps(fetch=False):
    """Review missing/exceptional periods; standard diluted EPS remains an explicit standard concept."""
    from app.annual_financials import annual_periods
    from app.data_pipeline.sec_catalogue import identifiers, load_all_sec
    from app.data_pipeline.sec_history import HISTORY_INDEX, prepare_company_facts
    from app.eps import eps_rules
    from app.revenue import revenue_rules

    issuers = identifiers()
    history = read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    revenue = revenue_rules()
    evidence = read_json(EVIDENCE_INDEX) if EVIDENCE_INDEX.exists() else {}
    existing = eps_rules()
    calendar = fiscal_rules()
    policy = {
        "version": 1,
        "selection_sha256": sha256(SELECTION),
        "periods": {},
        "unresolved": {},
    }
    supplement = {"version": 1, "selection_sha256": sha256(SELECTION), "periods": {}}
    counts = Counter()
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        facts = prepare_company_facts(
            entry, body, issuers[ticker].get("history"), history
        )
        periods = annual_periods(facts)
        for period in periods:
            start, end = period.period_start, period.period_end
            cell = period.metrics["eps_diluted"]
            counts["periods"] += 1
            ciks = {
                fact["source_cik"]
                for fact in facts
                if fact["history_eligible"]
                and fact["period_kind"] == "annual"
                and canonical_period(fact, calendar) == (start, end)
            }
            if len(ciks) != 1:
                raise ValueError(f"EPS issuer overlap: {ticker}:{end}")
            cik = next(iter(ciks))
            key = f"{cik}:{start}:{end}"
            if (
                cell.status == "ready"
                and key not in existing
                and key != AXON_REVISION_KEY
            ):
                counts["standard_ready"] += 1
                counts["diluted"] += 1
                continue
            try:
                if key == AXON_REVISION_KEY:
                    rule, record = review_axon_revision(facts, evidence, fetch)
                    policy["periods"][key] = rule
                    supplement["periods"][key] = record
                    counts["ready"] += 1
                    counts["diluted"] += 1
                    counts["exception_periods"] += 1
                    continue
                source_rule = revenue[key]
                # Reuse the verified latest income statement, not an arbitrary EPS note or segment table.
                statement_ref = source_rule["evidence"]
                summary_ref = source_rule["summary"]
                html = verify_reference(statement_ref).read_text(encoding="utf-8")
                tag, position, status, note = eps_position(html, cik, end)
                accession = source_rule["accession"]
                source_key = cik + ":" + accession
                cached_entry = evidence.setdefault(source_key, {})
                cached_entry.update(summary=summary_ref, statement=statement_ref)
                if cik != entry["cik"]:
                    raise ValueError(
                        "Exceptional predecessor EPS needs a separately reviewed source"
                    )
                candidates, _ = extract_facts(body, metrics={"eps_diluted": (tag,)})
                candidates = [
                    dict(fact, source_cik=cik)
                    for fact in candidates
                    if fact["accession"] == accession
                    and canonical_period(dict(fact, source_cik=cik), calendar)
                    == (start, end)
                ]
                source = entry["facts"]
                source_kind = "companyfacts"
                if not candidates or cik in CLASS_MEMBERS:
                    if "instance" not in cached_entry and not fetch:
                        raise ValueError(
                            "Original class-specific EPS instance is not cached; use --fetch"
                        )
                    if fetch:
                        cache_instance(cik, accession, cached_entry)
                        write_json(EVIDENCE_INDEX, evidence)
                    metadata = {
                        "accession": accession,
                        "filed": date.fromisoformat(source_rule["filed"]),
                        "form": "10-K",
                        "fiscal_period": "FY",
                    }
                    # The filing form comes from its source facts, including amendments.
                    filing_facts = [
                        fact
                        for fact in facts
                        if fact["source_cik"] == cik and fact["accession"] == accession
                    ]
                    if not filing_facts:
                        raise ValueError("EPS source filing metadata is unavailable")
                    metadata["form"] = filing_facts[0]["form"]
                    metadata["fiscal_period"] = filing_facts[0].get("fiscal_period")
                    candidates = [
                        fact
                        for fact in instance_eps(
                            verify_reference(cached_entry["instance"]).read_bytes(),
                            cik,
                            metadata,
                        )
                        if fact["tag"] == tag
                        and canonical_period(fact, calendar) == (start, end)
                    ]
                    source = cached_entry["instance"]
                    source_kind = "instance"
                matching = [
                    fact
                    for fact in candidates
                    if matches_rendered_value(position, fact["value"], end)
                ]
                if len(matching) != 1:
                    raise ValueError(
                        "EPS source fact does not match the unique annual statement value"
                    )
                fact = matching[0]
                record = {
                    "source_kind": source_kind,
                    "source": source,
                    "fact": json_fact(fact),
                }
                source_fact(record)
                supplement["periods"][key] = record
                updated_facts = list(facts)
                identity = fact_identity(fact)
                if not any(
                    fact_identity(existing_fact) == identity
                    for existing_fact in facts
                    if existing_fact["metric"] == "eps_diluted"
                ):
                    updated_facts.append(dict(fact, history_eligible=True))
                updated_period = next(
                    candidate
                    for candidate in annual_periods(updated_facts)
                    if candidate.period_start == start and candidate.period_end == end
                )
                variants = [
                    variant.model_copy(
                        update={
                            "statement_url": None,
                            "statement_label": None,
                            "statement_column": None,
                        }
                    )
                    for variant in updated_period.metrics["eps_diluted"].variants
                ]
                policy["periods"][key] = {
                    "ticker": ticker,
                    "status": status,
                    "eps_basis": position["eps_basis"],
                    "observed_sha256": variants_fingerprint(variants),
                    "fact": identity,
                    "note": note,
                    "statement": statement_ref,
                    "summary": summary_ref,
                    "label": position["label"],
                    "column": f"12 months ended {end}; USD per share; selected security class",
                    "source": source,
                }
                counts[status] += 1
                counts[position["eps_basis"]] += 1
                counts["exception_periods"] += 1
            except (ValueError, KeyError) as exc:
                policy["unresolved"][key] = {"ticker": ticker, "reason": str(exc)}
                counts["unresolved"] += 1
        if any(
            company_key.startswith(entry["cik"] + ":")
            for company_key in policy["periods"]
        ) or ticker in {issue["ticker"] for issue in policy["unresolved"].values()}:
            print(
                f"EPS review: {ticker}; {counts['exception_periods']} reviewed exceptions, {counts['unresolved']} unresolved so far",
                flush=True,
            )
    write_json(EVIDENCE_INDEX, evidence)
    write_json(FACTS_INDEX, supplement)
    write_json(RULES_PATH, policy)
    return {
        "status": "completed_with_gaps" if policy["unresolved"] else "reviewed",
        **counts,
        "issues": policy["unresolved"],
    }
