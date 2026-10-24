"""Verify fiscal-year labels from SEC cover pages without changing reported facts."""

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from threading import Event, Lock
from time import monotonic, sleep

from app.data_pipeline.identifiers import cached, write_json
from app.data_pipeline.revenue import ANNUAL_FORMS, EVIDENCE_INDEX
from app.data_pipeline.revenue_facts import cache_instance
from app.data_pipeline.sec import CACHE, METRICS, period_kind
from app.data_pipeline.sec_catalogue import identifiers, load_all_sec, verify_reference
from app.data_pipeline.sec_history import (
    HISTORY_INDEX,
    prepare_company_facts,
    read_predecessor,
)
from app.data_pipeline.sec_statements import DocumentText, StatementRows
from app.data_pipeline.source_files import SELECTION, read_json, sha256
from app.fiscal_periods import RULES_PATH, period_key

COVER_INDEX = CACHE / "fiscal-evidence-index.json"


def cover_filename(summary):
    """Locate the actual document/entity report, rather than assuming R1 is a cover."""
    files = []
    for report in ET.fromstring(summary).iter("Report"):
        name = (report.findtext("ShortName") or "").lower()
        filename = report.findtext("HtmlFileName") or ""
        is_cover = (
            "document and entity" in name
            or "document entity" in name
            or "document cover" in name
            or "document - cover" in name
            or name.startswith("cover")
            or name in ("dei document", "document")
        )
        if (
            is_cover
            and "parenthetical" not in name
            and re.fullmatch(r"R[0-9]+[.]htm", filename)
        ):
            files.append(filename)
    if len(files) != 1:
        raise ValueError("No unique SEC cover report")
    return files[0]


def parse_cover(html, expected_cik):
    """Verify the issuer and a common annual fiscal year, including joint filings."""
    parser = StatementRows()
    parser.feed(html)
    fields = defaultdict(set)
    for row in parser.rows:
        for taxonomy, tag in row["tags"]:
            if taxonomy == "dei" and len(row["cells"]) > 1:
                values = [cell for cell in row["cells"][1:] if cell.strip()]
                if values:
                    fields[tag].add(values[0].strip())
    required = ("DocumentType", "DocumentFiscalYearFocus", "DocumentPeriodEndDate")
    if any(len(fields[tag]) != 1 for tag in required):
        raise ValueError("Cover has missing or conflicting issuer/fiscal-year fields")
    values = {tag: next(iter(fields[tag])) for tag in required}
    ciks = {value.zfill(10) for value in fields["EntityCentralIndexKey"]}
    if expected_cik not in ciks:
        raise ValueError("Fiscal cover issuer does not match source CIK")
    if values["DocumentType"] not in ANNUAL_FORMS:
        raise ValueError("Fiscal cover is not an annual filing")
    year = int(values["DocumentFiscalYearFocus"])
    if not 1900 <= year <= 2100:
        raise ValueError("Invalid fiscal year")
    end_text = values["DocumentPeriodEndDate"].replace(".", "")
    end = None
    for pattern in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            end = datetime.strptime(end_text, pattern).date()
            break
        except ValueError:
            pass
    if end is None:
        raise ValueError("Unrecognized document-end date")
    return {"fiscal_year": year, "document_end": end.isoformat()}


def parse_instance_cover(body, expected_cik):
    """Read a missing rendered cover from original DEI facts for the exact issuer."""
    root = ET.fromstring(body)
    namespace = "{http://www.xbrl.org/2003/instance}"
    contexts = set()
    for context in root.findall(namespace + "context"):
        identifier = context.findtext(namespace + "entity/" + namespace + "identifier")
        if identifier and identifier.zfill(10) == expected_cik:
            contexts.add(context.attrib["id"])
    fields = defaultdict(set)
    tags = ("DocumentFiscalYearFocus", "DocumentPeriodEndDate", "DocumentType")
    for element in root:
        tag = element.tag.split("}")[-1]
        if tag in tags and element.get("contextRef") in contexts and element.text:
            fields[tag].add(element.text.strip())
    if any(len(fields[tag]) != 1 for tag in tags):
        raise ValueError("Missing or conflicting original DEI cover facts")
    if next(iter(fields["DocumentType"])) not in ANNUAL_FORMS:
        raise ValueError("Original DEI document is not annual")
    year = int(next(iter(fields["DocumentFiscalYearFocus"])))
    end = date.fromisoformat(next(iter(fields["DocumentPeriodEndDate"])))
    if not 1900 <= year <= 2100:
        raise ValueError("Invalid original fiscal year")
    return {"fiscal_year": year, "document_end": str(end)}


def fetch_covers(evidence, previous):
    """Cache compact cover pages at at most four requests per second, stopping on 403."""
    result = dict(previous)
    gate = Lock()
    stopped = Event()
    last_request = [0.0]

    def fetch_one(item):
        source, filing = item
        summary = filing.get("summary")
        if not summary:
            return source, None
        try:
            name = cover_filename(verify_reference(summary).read_bytes())
        except ValueError as exc:
            if "checksum" in str(exc):
                raise
            if "No unique" not in str(exc):
                raise
            old = dict(result.get(source, {}))
            if old.get("instance"):
                verify_reference(old["instance"])
                return source, old
            # Fetch multi-request original instances sequentially after the cover
            # workers finish, so they cannot exceed the shared request rate.
            old["summary"] = summary
            old["requires_instance"] = True
            return source, old
        old = result.get(source, {}).get("cover", filing.get("cover"))
        url = summary["url"].rsplit("/", 1)[0] + "/" + name
        if old:
            verify_reference(old)
            if old["url"] == url:
                return source, {
                    **result.get(source, {}),
                    "summary": summary,
                    "cover": old,
                }
        with gate:
            if stopped.is_set():
                raise ValueError("Fiscal cover downloads stopped")
            sleep(max(0, last_request[0] + 0.25 - monotonic()))
            if stopped.is_set():
                raise ValueError("Fiscal cover downloads stopped")
            last_request[0] = monotonic()
        try:
            cover = cached(url, json_response=False)
        except ValueError as exc:
            if "HTTP 403" in str(exc):
                stopped.set()
                raise
            return source, {"summary": summary, "issue": str(exc)}
        return source, {**result.get(source, {}), "summary": summary, "cover": cover}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_one, item) for item in evidence.items()]
        try:
            for count, future in enumerate(as_completed(futures), 1):
                source, entry = future.result()
                if entry:
                    result[source] = entry
                if count % 25 == 0:
                    write_json(COVER_INDEX, result)
                    print(f"Fiscal covers: {count}/{len(futures)}", flush=True)
        except BaseException:
            stopped.set()
            for future in futures:
                future.cancel()
            raise
        finally:
            write_json(COVER_INDEX, result)
    for source, entry in result.items():
        if entry.get("requires_instance"):
            cik, accession = source.split(":")
            cache_instance(cik, accession, entry)
            entry.pop("requires_instance")
            write_json(COVER_INDEX, result)
    return result


def raw_annual_filings(body):
    """Collect exact annual dates, including the current year of comparison filings."""
    groups = defaultdict(list)
    for metric, tags in METRICS.items():
        if metric == "equity":
            continue
        unit = "USD/shares" if metric == "eps_diluted" else "USD"
        for tag in tags:
            for fact in (
                body.get("facts", {})
                .get("us-gaap", {})
                .get(tag, {})
                .get("units", {})
                .get(unit, [])
            ):
                if fact.get("form") not in ANNUAL_FORMS or not fact.get("start"):
                    continue
                start = date.fromisoformat(fact["start"])
                end = date.fromisoformat(fact["end"])
                if period_kind(start, end) == "annual":
                    groups[fact["accn"]].append(fact)
    return groups


COVER_YEAR_CORRECTIONS = {
    "0000773840:0000773840-22-000018": {
        "reported_year": 2020,
        "fiscal_year": 2021,
        "document": "hon-20211231.htm",
    },
    "0000831259:0000831259-25-000006": {
        "reported_year": 2023,
        "fiscal_year": 2024,
        "document": "fcx-20241231.htm",
    },
    "0000056873:0001558370-24-004603": {
        "reported_year": 2024,
        "fiscal_year": 2023,
        "document": "kr-20240203x10k.htm",
        "document_end": "2024-02-03",
        "quote": (
            "All references to 2023, 2022 and 2021 are to the fiscal years ended February 3, 2024, "
            "January 28, 2023 and January 29, 2022, respectively"
        ),
    },
}


def cache_reviewed_documents(covers, evidence):
    """Cache the original reports needed to verify explicit metadata exceptions."""
    for source, correction in COVER_YEAR_CORRECTIONS.items():
        entry = covers[source]
        base = entry["summary"]["url"].rsplit("/", 1)[0] + "/"
        entry["original_report"] = cached(
            base + correction["document"],
            entry.get("original_report"),
            json_response=False,
        )
        write_json(COVER_INDEX, covers)
    source = "0000315189:0001047469-17-007719"
    entry = evidence[source]
    base = entry["summary"]["url"].rsplit("/", 1)[0] + "/"
    entry["original_report"] = cached(
        base + "a2233505z10-k.htm",
        entry.get("original_report"),
        json_response=False,
    )
    write_json(EVIDENCE_INDEX, evidence)


def verified_cover(source, entry):
    """Resolve reviewed inconsistent year tags against explicit original-report wording."""
    cik = source.split(":")[0]
    if entry.get("cover"):
        cover = parse_cover(
            verify_reference(entry["cover"]).read_text(encoding="utf-8"), cik
        )
    else:
        cover = parse_instance_cover(
            verify_reference(entry["instance"]).read_bytes(), cik
        )
    correction = COVER_YEAR_CORRECTIONS.get(source)
    if not correction:
        return cover
    if cover["fiscal_year"] != correction["reported_year"]:
        raise ValueError("Reviewed stale fiscal-year tag no longer matches its source")
    reference = entry.get("original_report")
    if not reference:
        raise ValueError("Missing original report for fiscal-year correction")
    parser = DocumentText()
    parser.feed(verify_reference(reference).read_text(encoding="utf-8"))
    text = " ".join(" ".join(parser.parts).split()).lower()
    text = re.sub(r"\s+,", ",", text)
    year = correction["fiscal_year"]
    heading = correction.get(
        "quote", f"for the fiscal year ended december 31, {year}"
    ).lower()
    expected_end = correction.get("document_end", f"{year}-12-31")
    if heading not in text or cover["document_end"] != expected_end:
        raise ValueError(
            "Original annual-report heading does not confirm the fiscal year"
        )
    return {**cover, "fiscal_year": year}


def label_candidates(body, cik, covers):
    """Use an explicit fiscal-year focus; compare only nearby full-year periods."""
    result = defaultdict(list)
    for accession, facts in raw_annual_filings(body).items():
        source = cik + ":" + accession
        entry = covers.get(source)
        if not entry:
            continue
        if not entry.get("cover") and not entry.get("instance"):
            continue
        cover = verified_cover(source, entry)
        document_end = date.fromisoformat(cover["document_end"])
        current_end = max(date.fromisoformat(fact["end"]) for fact in facts)
        if abs((current_end - document_end).days) > 7:
            continue
        for start, end in {(fact["start"], fact["end"]) for fact in facts}:
            distance = (current_end - date.fromisoformat(end)).days
            years_back = round(distance / 365.2425)
            if not 0 <= years_back <= 2 or abs(distance - years_back * 365.2425) > 14:
                continue
            key = period_key(cik, start, end)
            result[key].append(
                {
                    "fiscal_year": cover["fiscal_year"] - years_back,
                    "source": source,
                    "filed": max(fact["filed"] for fact in facts),
                }
            )
    return result


def reviewed_aliases(policy, evidence):
    """Verify Deere's explicit 52-week calendar before combining its two date spellings."""
    if not any(key.startswith("0000315189:") for key in policy["periods"]):
        return {}
    source = "0000315189:0001047469-17-007719"
    filing = evidence.get(source, {})
    original = filing.get("original_report")
    if not original:
        raise ValueError("Missing original Deere calendar evidence")
    parser = DocumentText()
    parser.feed(verify_reference(original).read_text(encoding="utf-8"))
    text = " ".join(" ".join(parser.parts).split())
    quote = (
        "The fiscal year ends for 2017, 2016, and 2015 were October 29, 2017, "
        "October 30, 2016, and November 1, 2015, respectively."
    )
    if quote not in text:
        raise ValueError(
            "Deere fiscal-calendar evidence does not match the reviewed disclosure"
        )
    aliases = [
        ("2014-11-01", "2015-10-31", "2014-11-03", "2015-11-01", 2015),
        ("2015-11-01", "2016-10-31", "2015-11-02", "2016-10-30", 2016),
    ]
    result = {}
    for old_start, old_end, start, end, year in aliases:
        old_key = period_key("0000315189", old_start, old_end)
        key = period_key("0000315189", start, end)
        if (
            policy["periods"].get(old_key, {}).get("fiscal_year") != year
            or policy["periods"].get(key, {}).get("fiscal_year") != year
        ):
            raise ValueError("Deere calendar alias lacks agreement on its fiscal year")
        result[old_key] = {
            "period_start": start,
            "period_end": end,
            "note": (
                "Earlier filings used nominal October dates for the same fiscal year. "
                "The row follows the actual 52-week calendar disclosed in the 2017 annual report; "
                "Sources retain each fact's original dates."
            ),
            "evidence": original,
            "statement": filing["R2.htm"],
        }
    return result


def review_fiscal_periods(fetch=False):
    """Publish only fully supported year labels; source dates are preserved."""
    evidence = read_json(EVIDENCE_INDEX)
    covers = read_json(COVER_INDEX) if COVER_INDEX.exists() else {}
    if fetch:
        covers = fetch_covers(evidence, covers)
        cache_reviewed_documents(covers, evidence)
    issuers = identifiers()
    history_index = (
        read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    )
    policy = {
        "version": 1,
        "selection_sha256": sha256(SELECTION),
        "periods": {},
        "sources": {},
        "aliases": {},
    }
    issues = []
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        history = issuers[ticker].get("history")
        sources = [(entry["cik"], body)]
        for predecessor in (history or {}).get("predecessors", []):
            sources.append(
                (predecessor["cik"], read_predecessor(predecessor, history_index))
            )
        candidates = {}
        for cik, response in sources:
            candidates.update(label_candidates(response, cik, covers))
        facts = prepare_company_facts(entry, body, history, history_index)
        targets = {
            period_key(fact["source_cik"], fact["period_start"], fact["period_end"])
            for fact in facts
            if fact["history_eligible"]
            and fact["form"] in ANNUAL_FORMS
            and fact["period_kind"] == "annual"
        }
        for key in sorted(targets):
            choices = candidates.get(key, [])
            years = {choice["fiscal_year"] for choice in choices}
            if len(years) != 1:
                issues.append({"ticker": ticker, "period": key, "candidates": choices})
                continue
            selected = max(
                choices, key=lambda choice: (choice["filed"], choice["source"])
            )
            policy["periods"][key] = {
                "fiscal_year": selected["fiscal_year"],
                "source": selected["source"],
            }
            policy["sources"][selected["source"]] = covers[selected["source"]]
    if issues:
        return {
            "status": "needs_review",
            "periods": len(policy["periods"]),
            "issues": issues,
        }
    policy["aliases"] = reviewed_aliases(policy, evidence)
    policy["cover_corrections"] = COVER_YEAR_CORRECTIONS
    # Check year identity after applying only the exact reviewed period aliases.
    seen_years = {}
    for key, entry in policy["periods"].items():
        cik, start, end = key.split(":")
        alias = policy["aliases"].get(key, {})
        canonical = (alias.get("period_start", start), alias.get("period_end", end))
        identity = (cik, entry["fiscal_year"])
        if identity in seen_years and seen_years[identity] != canonical:
            raise ValueError(f"Distinct periods have the same fiscal year: {identity}")
        seen_years[identity] = canonical
    by_issuer = defaultdict(list)
    for (cik, year), dates in seen_years.items():
        by_issuer[cik].append((date.fromisoformat(dates[1]), year))
    for cik, periods in by_issuer.items():
        ordered = sorted(periods)
        for (earlier_end, earlier_year), (later_end, later_year) in zip(
            ordered, ordered[1:]
        ):
            years_apart = round((later_end - earlier_end).days / 365.2425)
            if later_year - earlier_year != years_apart:
                raise ValueError(f"Fiscal-year sequence requires review: {cik}")
    write_json(RULES_PATH, policy)
    return {
        "status": "reviewed",
        "periods": len(policy["periods"]),
        "issuers": len(issuers),
    }
