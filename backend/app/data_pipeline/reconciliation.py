"""Compare companyfacts values to entity-wide inline XBRL in annual SEC documents."""

import json
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from app.data_pipeline.sec import CACHE, cache_response, extract_facts, load_pilot
from app.data_pipeline.source_files import ROOT, read_json, sha256


class InlineFacts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts, self.facts = {}, []
        self.context, self.part = None, None
        self.fact: dict[str, Any] | None = None
        self.excluded = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        local = tag.split(":")[-1]
        if local == "context":
            self.context = {"id": attrs.get("id"), "dimensioned": False}
        elif self.context is not None:
            if local in ("startdate", "enddate", "instant"):
                self.part = local
                self.context[local] = ""
            elif local in ("explicitmember", "typedmember", "segment", "scenario"):
                self.context["dimensioned"] = True
        if tag == "ix:nonfraction":
            self.fact = dict(attrs, text="")
        if tag == "ix:exclude":
            self.excluded += 1

    def handle_data(self, data):
        if self.context is not None and self.part:
            self.context[self.part] += data
        if self.fact is not None and not self.excluded:
            self.fact["text"] += data

    def handle_endtag(self, tag):
        local = tag.split(":")[-1]
        if local == "context" and self.context is not None:
            self.contexts[self.context["id"]] = self.context
            self.context, self.part = None, None
        elif local in ("startdate", "enddate", "instant"):
            self.part = None
        if tag == "ix:nonfraction" and self.fact is not None:
            self.facts.append(self.fact)
            self.fact = None
        if tag == "ix:exclude":
            self.excluded = max(0, self.excluded - 1)

    def matching_values(self, tag, start, end):
        result = set()
        for fact in self.facts:
            if fact.get("name") != "us-gaap:" + tag:
                continue
            context = self.contexts.get(fact.get("contextref"), {})
            if (
                context.get("dimensioned")
                or context.get("startdate", "").strip() != str(start)
                or context.get("enddate", "").strip() != str(end)
            ):
                continue
            transform = fact.get("format", "").split(":")[-1].lower()
            if transform and transform not in (
                "num-dot-decimal",
                "numdotdecimal",
                "zerodash",
                "fixed-zero",
            ):
                continue
            text = "".join(fact["text"].split()).replace(",", "")
            if text in ("-", "—", "–"):
                text = "0"
            negative = text.startswith("(") and text.endswith(")")
            text = text.strip("()")
            try:
                value = Decimal(text) * Decimal(10) ** int(fact.get("scale", "0"))
                if fact.get("sign") == "-" or negative:
                    value = -abs(value)
                result.add(value)
            except (InvalidOperation, ValueError):
                continue
        return result


def annual_candidate(submission):
    candidates = []
    for index, form in enumerate(submission["form"]):
        report_date = submission["reportDate"][index]
        if form != "10-K" or not "2024-01-01" <= report_date <= "2024-12-31":
            continue
        candidates.append(
            {
                "report_date": report_date,
                "accession": submission["accessionNumber"][index],
                "document": submission["primaryDocument"][index],
            }
        )
    return candidates


def fetch_annual_documents(pilot):
    from app.core.config import settings

    if not settings.sec_user_agent:
        raise ValueError("Set SEC_USER_AGENT before fetching annual reports")
    index = {}
    for ticker, (entry, _) in pilot.items():
        sub = read_json(CACHE / entry["submissions"]["file"])
        candidates = annual_candidate(sub["filings"]["recent"])
        for old in sub["filings"]["files"]:
            if candidates:
                break
            if old["filingFrom"] <= "2025-12-31" and old["filingTo"] >= "2024-01-01":
                cached = cache_response(
                    "https://data.sec.gov/submissions/" + old["name"],
                    settings.sec_user_agent,
                )
                candidates.extend(annual_candidate(read_json(CACHE / cached["file"])))
        if not candidates:
            raise ValueError(f"No 2024 annual SEC document found: {ticker}")
        chosen = max(candidates, key=lambda c: (c["report_date"], c["accession"]))
        url = f"https://www.sec.gov/Archives/edgar/data/{int(entry['cik'])}/{chosen['accession'].replace('-', '')}/{chosen['document']}"
        cached = cache_response(url, settings.sec_user_agent, json_response=False)
        index[ticker] = dict(chosen, **cached)
        print(f"Cached annual SEC document: {ticker}", flush=True)
    (CACHE / "annual-index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    return index


def reconcile_sec():
    _, pilot = load_pilot()
    index_path = CACHE / "annual-index.json"
    index = (
        read_json(index_path) if index_path.exists() else fetch_annual_documents(pilot)
    )
    if set(index) != set(pilot):
        raise ValueError("Annual document cache must contain all ten pilot companies")
    report = {
        "method": (
            "Compare original companyfacts accession to entity-wide inline XBRL "
            "in the primary 2024 annual report. Values include reported scale and sign; "
            "no currency conversion."
        ),
        "companies": {},
    }
    complete = True
    for ticker, (_, body) in pilot.items():
        document = index[ticker]
        path = CACHE / document["file"]
        if path.parent != CACHE or sha256(path) != document["sha256"]:
            raise ValueError(f"Annual document checksum mismatch: {ticker}")
        parser = InlineFacts()
        parser.feed(path.read_text(encoding="utf-8"))
        facts, _ = extract_facts(body)
        checks = {}
        for metric in ("revenue", "net_income"):
            eligible = [
                fact
                for fact in facts
                if fact["metric"] == metric
                and fact["accession"] == document["accession"]
                and fact["period_kind"] == "annual"
                and str(fact["period_end"]) == document["report_date"]
            ]
            checks[metric] = []
            for fact in eligible:
                observed = parser.matching_values(
                    fact["tag"], fact["period_start"], fact["period_end"]
                )
                matched = fact["value"] in observed
                checks[metric].append(
                    {
                        "tag": fact["tag"],
                        "value": str(fact["value"]),
                        "unit": fact["unit"],
                        "period_start": str(fact["period_start"]),
                        "period_end": str(fact["period_end"]),
                        "document_values": sorted(map(str, observed)),
                        "matched": matched,
                    }
                )
            if not checks[metric] or not all(c["matched"] for c in checks[metric]):
                complete = False
        report["companies"][ticker] = {
            "url": document["url"],
            "sha256": document["sha256"],
            "accession": document["accession"],
            "checks": checks,
        }
    report["status"] = "matched" if complete else "needs_review"
    output = ROOT / "data/processed/sec-annual-reconciliation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
