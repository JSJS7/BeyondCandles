"""Reproduce reviewed primary-report NCI controls using pinned rows and scope evidence."""

import re
from decimal import Decimal
from html.parser import HTMLParser

from app.data_pipeline.sec_catalogue import verify_reference
from app.data_pipeline.sec_statements import displayed_amount, normalized_label
from app.data_pipeline.source_files import ROOT, SELECTION, read_json, sha256
from app.fact_identity import variants_fingerprint

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
            # The pattern above admits only well-formed numbers, so none is None.
            values = [displayed_amount(number) for number in numbers]
            if values == expected:
                matches.append(
                    values[exception["value_column"]] * Decimal(exception["scale"])
                )
    if not matches or set(matches) != {Decimal(exception["value"])}:
        raise ValueError("The reviewed primary-report row or scale no longer matches")
    return matches[0]


def exceptions_document():
    """Read manual equity decisions only for the frozen company selection they reviewed."""
    document = read_json(EXCEPTIONS)
    if document["version"] != 1:
        raise ValueError("Unsupported primary equity exception version")
    if document.get("selection_sha256") != sha256(SELECTION):
        raise ValueError("Equity exceptions do not match the frozen selection")
    return document


def reviewed_nci_controls(task, cik, accession):
    """Verify four nested note-table columns against exact dates, rows and source hashes."""
    end = task["period"].period_end
    document = exceptions_document()
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
