"""Read SEC statement tables with the existing concept, date and scale rules."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, cast

from app.data_pipeline.revenue_facts import CUSTOM_REVENUE_TAGS

# Exact face-statement labels. Unknown wording and unreviewed custom concepts fail closed.

TOTAL_LABELS = {
    "revenue",
    "revenues",
    # General Dynamics' 2016 10-K renders its Revenues row as "Revenue, total".
    "revenue total",
    "total revenue",
    "total revenues",
    "net revenue",
    "net revenues",
    "sales",
    "net sales",
    "total sales",
    "total net sales",
    "sales net",
    "revenues net",
    "operating revenue",
    "operating revenues",
    "total operating revenues",
    "net operating revenues",
    "net operating revenue",
    "total net revenues",
    "total net revenue",
    "revenues net of interest expense",
    "total revenues net of interest expense",
    "total revenue net of interest expense",
    "revenue net of interest expense",
    "sales to customers",
    "net sales and revenues",
    "total sales and revenues",
    "sales and other operating revenue",
    "sales and other operating revenues",
    "railway operating revenues",
    "sales and revenues",
    "net sales and revenue",
    "total net sales and revenue",
    "total service revenues and vehicle sales",
    "service revenues and vehicle sales",
    "net revenues including net interest income",
    "revenue from contract with customer",
    "revenue from contracts with customers",
    "revenue from contract with customer excluding assessed tax",
    "revenue from contract with customer including assessed tax",
    "sales and other operating revenue including sales based taxes",
}


SCALES = {"thousands": 1000, "millions": 1000000, "billions": 1000000000}
# A rendered header date, e.g. "Dec. 31, 2024" or "December 31, 2024".
HEADER_DATE = r"[A-Za-z]+[.]? \d{1,2}, \d{4}"


class NoUniqueStatementRow(ValueError):
    """The statement has no single recognized row for the requested total or concept."""


def expand_columns(cells, spans):
    """Repeat a spanned cell as blanks, so every value lines up with its header column."""
    result = []
    for cell, span in zip(cells, spans, strict=True):
        result.extend([cell] + [""] * (span - 1))
    return result


def header_date(text):
    """Read the first header date in the text, or None if there is none."""
    match = re.search(f"({HEADER_DATE})", text)
    if not match:
        return None
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(match.group(1).replace(".", ""), pattern).date()
        except ValueError:
            continue
    return None


def displayed_amount(text):
    """Read a displayed amount; None when the text is not a number.

    "$", thousands separators and spaces are ignored, and parentheses mean a
    negative amount. The scale of the statement is applied by the caller.
    """
    compact = re.sub(r"[$,\s]", "", text).replace("(", "-").replace(")", "")
    try:
        return Decimal(compact)
    except InvalidOperation:
        return None


def stated_dollar_scale(header_text):
    """The scale of an explicit "$ in thousands/millions/billions", or None."""
    match = re.search(r"\$\s+in\s+(thousands|millions|billions)", header_text)
    if not match:
        return None
    return SCALES[match.group(1)]


VOID_ELEMENTS = {"br", "col", "hr", "img", "input", "link", "meta", "wbr"}


def hidden_style(attrs):
    style = re.sub(r"\s", "", (attrs.get("style") or "").lower())
    return "display:none" in style


class StatementRows(HTMLParser):
    """Read only the visible statement table, excluding hidden concept-definition tables.

    SEC renderings of early 2015 also put the concept name in a hidden span inside
    every value cell ("708.1" followed by "us-gaap_..."); hidden text is not part of
    the displayed value.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.rows = []
        self.row: dict[str, Any] | None = None
        self.cell = None
        # Open elements inside a hidden element of a cell, the hidden one included.
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            if self.depth or "report" in (attrs.get("class") or "").split():
                self.depth += 1
        if self.depth != 1:
            return
        if self.cell is not None and tag not in VOID_ELEMENTS:
            if self.hidden or hidden_style(attrs):
                self.hidden += 1
        if tag == "tr":
            self.row = {"cells": [], "spans": [], "tags": set(), "header": False}
        if self.row is not None:
            if tag in ("td", "th"):
                self.cell = ""
                self.row["spans"].append(int(attrs.get("colspan") or "1"))
                if tag == "th":
                    self.row["header"] = True
            reference = re.search(
                r"defref_([A-Za-z0-9-]+)_([A-Za-z0-9]+)", attrs.get("onclick") or ""
            )
            if reference:
                self.row["tags"].add(reference.groups())

    def handle_data(self, text):
        if self.depth == 1 and self.cell is not None and not self.hidden:
            self.cell += text

    def handle_endtag(self, tag):
        if self.depth == 1:
            if tag in ("td", "th"):
                # The cell ends even if a hidden element inside it was left unclosed.
                self.hidden = 0
            elif self.hidden and tag not in VOID_ELEMENTS:
                self.hidden -= 1
                return
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row["cells"].append(" ".join(self.cell.split()))
                self.cell = None
            if tag == "tr" and self.row is not None:
                self.rows.append(self.row)
                self.row = None
        if tag == "table" and self.depth:
            self.depth -= 1


class DocumentText(HTMLParser):
    """Read document text for the fiscal-period evidence verifier."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def normalized_label(value):
    value = re.sub(
        r"\[[^]]*\]|\((?:notes?\b[^)]*|includes?\s+\$[^)]*|\d+[a-z]?|[a-z])\)",
        "",
        value.lower(),
    )
    return " ".join(re.sub(r"[^a-z ]", " ", value).split())


def statement_total(
    html,
    end=None,
    concept=None,
    extra_labels=(),
    *,
    stop_at_costs=True,
    stop_at_dimensions=False,
    per_share=False,
):
    """Accept exactly one explicit total row; never infer a total from its numeric size."""
    parser = StatementRows()
    parser.feed(html)
    candidates = []
    headers = []
    date_headers = []
    duration_headers = []
    stub_columns = 0
    revenue_section = False
    for row in parser.rows:
        cells = row["cells"]
        if stop_at_dimensions and any(tag.endswith("Axis") for _, tag in row["tags"]):
            break
        if row["header"]:
            headers.extend(cells)
            if any("months ended" in cell.lower() for cell in cells):
                duration_headers = []
                for cell, span in zip(cells[1:], row["spans"][1:], strict=True):
                    duration_headers.extend(
                        [cell.lower().startswith("12 months")] * span
                    )
                stub_columns = row["spans"][0] - 1
            dates = []
            recognized_header = True
            for cell, span in zip(cells, row["spans"], strict=True):
                # Only a cell starting with a date names a date column.
                parsed = header_date(cell) if re.match(HEADER_DATE, cell) else None
                if parsed is None and cell and not re.fullmatch(r"\[\d+\]", cell):
                    recognized_header = False
                dates.extend([parsed] * span)
            if recognized_header and any(dates):
                date_headers = dates
        if (
            not stop_at_costs
            and not per_share
            and any(tag.startswith("EarningsPerShare") for _, tag in row["tags"])
        ):
            break
        # Later portions of this rendered table can repeat revenue by product/segment.
        if stop_at_costs and any(
            tag.startswith("CostOf")
            or tag
            in (
                "GrossProfit",
                "OperatingExpenses",
                "CostsAndExpenses",
                "IncomeTaxExpenseBenefit",
                "NetIncomeLoss",
                "ProfitLoss",
                "BenefitsLossesAndExpenses",
            )
            for _, tag in row["tags"]
        ):
            break
        if not cells or len(cells) < 2 or row["header"]:
            continue
        if any(tag == "RevenuesAbstract" for _, tag in row["tags"]):
            revenue_section = True
        label = normalized_label(cells[0])
        is_section_total = (
            revenue_section
            and label == "total"
            and row["tags"] == {("us-gaap", "Revenues")}
        )
        if concept is not None:
            recognized = row["tags"] == {("us-gaap", concept)}
        else:
            recognized = (
                label in TOTAL_LABELS or label in extra_labels or is_section_total
            )
        if not recognized:
            continue
        tags = row["tags"]
        if len(tags) == 1 and any(re.search(r"\d", cell) for cell in cells[1:]):
            taxonomy, tag = next(iter(tags))
            values = expand_columns(cells[1:], row["spans"][1:])
            candidates.append(
                {
                    "taxonomy": taxonomy,
                    "tag": tag,
                    "label": cells[0],
                    "values": values[stub_columns:],
                }
            )
    if end is not None:
        # Values may still contain a footnote column here; lengths are checked after trimming.
        candidates = [
            candidate
            for candidate in candidates
            if any(
                column_end == end and re.search(r"\d", value)
                for column_end, value in zip(
                    date_headers, candidate["values"], strict=False
                )
            )
        ]
    # Banks present their GAAP revenue after interest expense. Preserve the gross
    # revenue as another source, rather than treating the two definitions as aliases.
    net_interest_totals = [
        candidate
        for candidate in candidates
        if candidate["tag"] == "RevenuesNetOfInterestExpense"
    ]
    if len(net_interest_totals) == 1:
        candidates = net_interest_totals
    # A face statement may show both contract revenue and an explicit total including leases.
    explicit_totals = [
        candidate
        for candidate in candidates
        if (
            normalized_label(candidate["label"]).startswith("total ")
            or normalized_label(candidate["label"]) == "total"
        )
    ]
    net_totals = [
        candidate
        for candidate in candidates
        if normalized_label(candidate["label"]) == "total net revenues"
    ]
    if len(net_totals) == 1:
        candidates = net_totals
        explicit_totals = net_totals
    if len(explicit_totals) == 1:
        candidates = explicit_totals
    if len(candidates) != 1:
        if concept is None:
            raise NoUniqueStatementRow(
                "Income statement has no unique recognized total-revenue row"
            )
        raise NoUniqueStatementRow(f"Income statement has no unique {concept} row")
    total = candidates[0]
    if (
        total["taxonomy"] != "us-gaap"
        and (total["taxonomy"], total["tag"]) not in CUSTOM_REVENUE_TAGS
    ):
        raise ValueError("Unreviewed custom revenue concept")
    header = " ".join(headers).lower()
    if "usd" not in header:
        raise ValueError("Income statement currency is not explicitly USD")
    scale = stated_dollar_scale(header)
    if scale is None:
        # A share-count scale does not scale currency amounts (e.g. COR reports
        # dollar amounts in full and only its share counts in thousands).
        currency_header = re.sub(
            r"shares\s+in\s+(?:thousands|millions|billions)", "", header
        )
        scales = {name for name in SCALES if "in " + name in currency_header}
        if len(scales) > 1:
            raise ValueError("Ambiguous income statement scale")
        scale = SCALES[next(iter(scales))] if scales else 1
    total["scale"] = scale
    if per_share:
        # Statement totals may be in millions; EPS is already reported per share.
        total["scale"] = 1
    total["headers"] = headers
    # SEC sometimes inserts an empty footnote column before all monetary columns.
    while len(total["values"]) > len(date_headers) and total["values"][0] == "":
        total["values"].pop(0)
    if len(date_headers) != len(total["values"]):
        raise ValueError(
            "Income statement dates cannot be matched unambiguously to value columns"
        )
    if len(duration_headers) != len(date_headers) or not any(duration_headers):
        raise ValueError(
            "Income statement does not identify its annual columns unambiguously"
        )
    total["dates"] = date_headers
    total["annual_columns"] = duration_headers
    return total


def matches_rendered_value(total, value, end):
    """Allow displayed rounding to the statement scale, never an arbitrary percentage tolerance."""
    for column_end, annual, cell in zip(
        total["dates"], total["annual_columns"], total["values"], strict=True
    ):
        if column_end != end or not annual:
            continue
        number = displayed_amount(cell)
        if number is None:
            continue
        displayed = number * total["scale"]
        rounding_unit = Decimal(total["scale"]) * Decimal(10) ** cast(
            int, number.as_tuple().exponent
        )
        if abs(displayed - value) <= rounding_unit / 2:
            return True
    return False
