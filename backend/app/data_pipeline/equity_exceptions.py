"""Reproduce reviewed primary-report exceptions using pinned rows and scope evidence."""

import re
from decimal import Decimal
from html.parser import HTMLParser

from app.data_pipeline.sec_catalogue import verify_reference
from app.data_pipeline.sec_statements import normalized_label
from app.data_pipeline.source_files import ROOT, read_json
from app.equity import fact_identity, variants_fingerprint
from app.fiscal_periods import canonical_period

EXCEPTIONS = ROOT / "data/equity-exceptions.json"


class OriginalTables(HTMLParser):
    """Keep original visible table rows separate, including nested financial tables."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.tables = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        if not self.stack:
            return
        state = self.stack[-1]
        if tag == "tr":
            state["row"] = []
        if tag in ("td", "th"):
            state["cell"] = []

    def handle_data(self, text):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(text)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        state = self.stack[-1]
        if tag in ("td", "th") and state["cell"] is not None:
            if state["row"] is not None:
                state["row"].append(" ".join("".join(state["cell"]).split()))
            state["cell"] = None
        if tag == "tr" and state["row"] is not None:
            state["rows"].append(state["row"])
            state["row"] = None
        if tag == "table":
            self.tables.append(self.stack.pop()["rows"])


def verify_original_position(body, exception):
    """Require the independently reviewed full row, headers, numeric column and scale."""
    parser = OriginalTables()
    parser.feed(body)
    expected = [Decimal(number) for number in exception["row_numbers"]]
    matches = []
    for table in parser.tables:
        table_text = " ".join(" ".join(row) for row in table).casefold()
        if not all(term.casefold() in table_text for term in exception["header_terms"]):
            continue
        for row in table:
            if not row or normalized_label(row[0]) != exception["row_label"]:
                continue
            text = " ".join(row[1:])
            numbers = re.findall(r"\(?\s*\d[\d,]*(?:[.]\d+)?\s*\)?", text)
            values = [
                Decimal(re.sub(r"[,\s]", "", number).replace("(", "-").replace(")", ""))
                for number in numbers
            ]
            if values == expected:
                matches.append(
                    values[exception["value_column"]] * Decimal(exception["scale"])
                )
    if not matches or set(matches) != {Decimal(exception["value"])}:
        raise ValueError("The reviewed primary-report row or scale no longer matches")
    return matches[0]


def manual_choice(task, calendar, fetch=False):
    """Never transfer a manual decision to another period, issuer or refreshed snapshot."""
    end = task["period"].period_end
    document = read_json(EXCEPTIONS)
    if document["version"] != 1:
        raise ValueError("Unsupported primary equity exception version")
    exception = document["positions"].get(f"{task['primary']}:{end}")
    if not exception or exception["observed_sha256"] != variants_fingerprint(
        task["variants"]
    ):
        return None
    reference = exception["evidence"]
    if fetch:
        from app.data_pipeline.identifiers import cached

        actual = cached(reference["url"], reference, json_response=False)
        if actual["sha256"] != reference["sha256"]:
            raise ValueError("A manually reviewed primary source changed")
    value = verify_original_position(
        verify_reference(reference).read_text(encoding="utf-8"), exception
    )
    for control in exception.get("corroborating_positions", []):
        evidence = control["evidence"]
        if fetch:
            from app.data_pipeline.identifiers import cached

            actual = cached(evidence["url"], evidence, json_response=False)
            if actual["sha256"] != evidence["sha256"]:
                raise ValueError("A corroborating primary equity source changed")
        confirmed = verify_original_position(
            verify_reference(evidence).read_text(encoding="utf-8"), control
        )
        if confirmed != value:
            raise ValueError(
                "The independent primary balance does not corroborate equity"
            )

    facts = [
        fact
        for fact in task["facts"]
        if fact.get("history_eligible", True)
        and fact["source_cik"] == exception["source_cik"]
        and canonical_period(fact, calendar)[1] == end
    ]
    direct = [
        fact
        for fact in facts
        if fact["metric"] == "equity"
        and fact["tag"] in ("StockholdersEquity", "PartnersCapital")
        and fact["value"] == value
    ]
    if direct:
        selected = max(direct, key=lambda fact: (fact["filed"], fact["accession"]))
        anchor = selected
        component = {"fact": fact_identity(selected), "coefficient": 1}
    else:
        anchors = [
            fact
            for fact in facts
            if fact["accession"] == exception["accession"] and fact["unit"] == "USD"
        ]
        if not anchors:
            raise ValueError(
                "No stored filing anchor for the reviewed primary statement"
            )
        anchor = anchors[0]
        selected = dict(
            anchor,
            tag=exception["tag"],
            value=value,
            period_start=anchor["period_end"],
            statement_url=reference["url"],
            statement_label=exception["row_label"],
            statement_column=f"FY{end.year} (reviewed primary table)",
        )
        component = {
            "fact": fact_identity(selected),
            "coefficient": 1,
            "statement_position": True,
        }
    return {
        "observed_sha256": exception["observed_sha256"],
        "value": format(value, "f"),
        "anchor": fact_identity(anchor),
        "filed": str(selected["filed"]),
        "accession": selected["accession"],
        "components": [component],
        "calculation": None,
        "note": exception["note"],
        "statement": reference,
        "manual_position": True,
        **(
            {"corroborating_positions": exception["corroborating_positions"]}
            if exception.get("corroborating_positions")
            else {}
        ),
    }


def reviewed_nci_controls(task, cik, accession):
    """Verify four nested note-table columns against exact dates, rows and source hashes."""
    end = task["period"].period_end
    document = read_json(EXCEPTIONS)
    if document["version"] != 1:
        raise ValueError("Unsupported primary equity exception version")
    control = document.get("nci_controls", {}).get(f"{task['primary']}:{end}")
    if not control or control["source_cik"] != cik or control["accession"] != accession:
        return []
    if control["observed_sha256"] != variants_fingerprint(task["variants"]):
        return []
    reference = control["evidence"]
    value = verify_original_position(
        verify_reference(reference).read_text(encoding="utf-8"), control
    )
    return [
        {
            "column": control["column_label"],
            "value": format(value, "f"),
            "statement": reference,
        }
    ]
