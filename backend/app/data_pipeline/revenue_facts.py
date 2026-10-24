"""Read exact reported revenue facts from original XBRL, including issuer custom tags."""

import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from typing import cast

from app.data_pipeline.sec import CACHE, period_kind
from app.data_pipeline.source_files import read_json

SUPPLEMENT_INDEX = CACHE / "revenue-facts-index.json"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
CUSTOM_REVENUE_TAGS = {
    ("bac", "RevenuesNetOfInterestExpenseBeforeProvisionForLoanLoss"),
    ("xom", "SalesAndOtherOperatingRevenueIncludingSalesBasedTaxes"),
    ("bk", "TotalRevenuesIncludingRevenueGeneratedByVariableInterestEntities"),
    ("amp", "NetRevenues"),
}


def instance_facts(body, cik, metadata, concepts):
    """Require consolidated contexts for the exact issuer, period and USD unit."""
    namespaces = dict(
        value for _, value in ET.iterparse(io.BytesIO(body), events=("start-ns",))
    )
    root = ET.fromstring(body)
    contexts = {}
    for context in root.findall(XBRLI + "context"):
        identifier = context.find(XBRLI + "entity/" + XBRLI + "identifier")
        if identifier is None or (identifier.text or "").strip().zfill(10) != cik:
            continue
        if (
            context.find(".//" + XBRLDI + "explicitMember") is not None
            or context.find(".//" + XBRLDI + "typedMember") is not None
        ):
            continue
        if context.find(XBRLI + "scenario") is not None:
            continue
        start = context.findtext(XBRLI + "period/" + XBRLI + "startDate")
        end = context.findtext(XBRLI + "period/" + XBRLI + "endDate")
        if start and end:
            contexts[context.attrib["id"]] = (
                date.fromisoformat(start),
                date.fromisoformat(end),
            )
    usd_units = set()
    for unit in root.findall(XBRLI + "unit"):
        measures = unit.findall(XBRLI + "measure")
        if len(measures) == 1 and (measures[0].text or "").split(":")[-1] == "USD":
            prefix = (measures[0].text or "").split(":")[0]
            if namespaces.get(prefix) == "http://www.xbrl.org/2003/iso4217":
                usd_units.add(unit.attrib["id"])
    names = {}
    for taxonomy, tag in concepts:
        namespace = namespaces.get(taxonomy)
        if namespace:
            names["{" + namespace + "}" + tag] = (taxonomy, tag)
    result = {}
    for element in root:
        if (
            element.tag not in names
            or element.get("contextRef") not in contexts
            or element.get("unitRef") not in usd_units
        ):
            continue
        if element.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        start, end = contexts[element.get("contextRef")]
        if (
            not date(2015, 1, 1) <= end <= date(2024, 12, 31)
            or period_kind(start, end) != "annual"
        ):
            continue
        value = Decimal((element.text or "").strip())
        if (
            not value.is_finite()
            or abs(value) >= Decimal("1e28")
            or cast(int, value.as_tuple().exponent) < -10
        ):
            raise ValueError("Original revenue fact exceeds the database numeric range")
        taxonomy, tag = names[element.tag]
        fact = {
            "source_cik": cik,
            "metric": "revenue",
            "taxonomy": taxonomy,
            "tag": tag,
            "unit": "USD",
            "value": value,
            "period_start": start,
            "period_end": end,
            "period_kind": "annual",
            **metadata,
        }
        key = (taxonomy, tag, start, end)
        if key in result and result[key]["value"] != value:
            raise ValueError(f"Conflicting consolidated original XBRL facts: {key}")
        result[key] = fact
    return list(result.values())


def supplemental_facts(ciks):
    """Reproduce supplemental facts offline; corrupt source files stop the whole import."""
    from app.data_pipeline.sec_catalogue import verify_reference

    if not SUPPLEMENT_INDEX.exists():
        return []
    index = read_json(SUPPLEMENT_INDEX)
    result = []
    for source, entry in index["filings"].items():
        cik, accession = source.split(":")
        if cik not in ciks:
            continue
        concepts = {tuple(concept) for concept in entry["concepts"]}
        if not concepts <= CUSTOM_REVENUE_TAGS:
            raise ValueError("Unapproved supplemental revenue concept")
        metadata = {
            "accession": accession,
            "filed": date.fromisoformat(entry["filed"]),
            "form": entry["form"],
            "fiscal_period": entry.get("fiscal_period"),
        }
        result.extend(
            instance_facts(
                verify_reference(entry["instance"]).read_bytes(),
                cik,
                metadata,
                concepts,
            )
        )
    return result


def cache_instance(cik, accession, evidence):
    """Discover the original instance in the SEC filing directory; never guess its contents."""
    from app.data_pipeline.identifiers import cached
    from app.data_pipeline.sec_catalogue import verify_reference

    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    evidence["directory"] = cached(base + "index.json", evidence.get("directory"))
    directory = json.loads(
        verify_reference(evidence["directory"]).read_text(encoding="utf-8")
    )
    candidates = [
        item["name"]
        for item in directory["directory"]["item"]
        if re.fullmatch(r"[A-Za-z0-9_.-]+[.]xml", item["name"])
        and not item["name"].endswith(("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml"))
        and item["name"] != "FilingSummary.xml"
    ]
    # SEC's extracted inline instance ends in _htm.xml. Earlier filings contain one ordinary instance.
    extracted = [name for name in candidates if name.endswith("_htm.xml")]
    candidates = extracted or candidates
    if len(candidates) != 1:
        raise ValueError(f"No unique original XBRL instance: {cik}:{accession}")
    evidence["instance"] = cached(
        base + candidates[0], evidence.get("instance"), json_response=False
    )
    return evidence["instance"]
