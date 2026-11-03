"""Compare every stored metric version with the tables of its own primary document.

XBRL facts and reviewed rules decide what the app serves; this check reads a different
form of the same report. A version passes when its value (in the document's scale; EPS
before split normalisation) stands in a row whose label fits the metric, in the column
or row label of its period. A calculated value passes when the result or every component
is found that way. Everything else goes to a queue that a person reviews.

It finds a wrong period, a wrong value and a wrong row. It cannot find a definition
choice: a value that really stands in a revenue row may still be the wrong revenue row.
The negative control measures how often a planted wrong value still passes.
"""

import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser

from app.data_pipeline.sec import fetch
from app.data_pipeline.sec_cache import user_agent
from app.data_pipeline.source_files import ROOT, read_json, write_json

EXTRACTS = ROOT / "data/raw/sec-extracts"
REVIEWS = ROOT / "data/document-check-reviews.json"
REPORT = ROOT / "data/processed/document-check-report.json"
RESULTS = ROOT / "data/processed/document-check-results.csv"
CONTROL_REPORT = ROOT / "data/processed/document-check-control.json"
EXAMPLES_PER_METRIC = 40
SMALLEST_PLANTED_AMOUNT = Decimal(100)

NUMBER = re.compile(
    r"^(?P<neg>\(|-|−|–)?\s*\$?\s*(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"\s*\)?\s*(?:\([a-z0-9]{1,2}\)|\*)*\s*$",
    re.I,
)
MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)
DATE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*(\d{1,2})\s*,?\s*((?:19|20)\d\d)",
    re.I,
)
YEAR = re.compile(r"(?<![\d,.])((?:19|20)\d\d)(?![\d,])")

ROW_LABELS = {
    "revenue": r"revenue|sales",
    "net_income": r"net (income|earnings|\(loss\)|loss|profit)|earnings|profit|income \(loss\)",
    "eps_diluted": r"dilut|per share|per common share|earnings|eps|net income|net loss|per average|per class",
    "equity": r"equity|deficit|deficiency|investment|capital|balance|net assets|end of"
    r"|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d",
    "operating_cash_flow": r"operat",
}
# Rows that contain a metric's words but report something else.
EXCLUDED_ROW_LABELS = {
    # "Total revenue, net of interest expense" is revenue; a row of expenses is not.
    "revenue": r"cost|(?<!interest )expens|liabilit|assets|deferred|unearned|per share"
    r"|percent|% of|margin",
    "net_income": r"noncontrolling|non-controlling|minority|cash|premium|per share|before"
    r"|comprehensive|operating|discontinued|gross|retained|undistributed",
    "eps_diluted": r"^(?!.*dilut).*basic|noncontrolling|non-controlling|minority|dividend|book value",
    "equity": r"noncontrolling|non-controlling|minority|liabilit|(?<!net )assets|working capital"
    r"|capital expenditure|paid-in|investments\b|investment (income|securities)",
    "operating_cash_flow": r"income|earnings|expens|profit|margin|lease|loss\b|revenue"
    r"|investing|financing|^other operation",
}
# Rows that are never a reported GAAP figure of any metric.
NOT_GAAP = r"non-gaap|non gaap|adjusted|pro forma|excluding"
# What a wrong row looks like per metric, for the negative control only.
WRONG_ROWS = {
    "revenue": r"^total (current )?(liabilities|assets)|^net (income|earnings)"
    r"|^total (costs|operating expenses)|^gross (profit|margin)|^operating income",
    "net_income": r"^total (net )?revenues?|^net sales|^operating income|^income before"
    r"|^comprehensive income|^gross (profit|margin)|^total assets",
    "eps_diluted": r"dividends? .*per|book value|^basic",
    "equity": r"^total liabilities$|^total assets|^goodwill|^total current",
    "operating_cash_flow": r"investing activities|financing activities"
    r"|^operating income|^net income|^total revenue",
}
AMOUNT_SCALES = (Decimal(1), Decimal(1000), Decimal(1_000_000), Decimal(1_000_000_000))
ONE_SCALE = (Decimal(1),)
# A value found with its label and in its period column is the only full match.
FULL_MATCH = 3
# Berkshire reports EPS for Class A; the stored value is the Class B equivalent.
BERKSHIRE_CLASS_A_MULTIPLE = 1500
DURATIONS = (
    ("three months", 3),
    ("six months", 6),
    ("nine months", 9),
    ("twelve months", 12),
    ("thirteen weeks", 3),
    ("twenty-six weeks", 6),
    ("thirty-nine weeks", 9),
    ("13 weeks", 3),
    ("26 weeks", 6),
    ("39 weeks", 9),
    ("quarter", 3),
    ("year", 12),
)
SKIPPED_CELLS = {"", "$", "%", "​", "€"}
# A cell holding only footnote marks ("*", "(a)(b)") is not a column of values.
FOOTNOTE_CELL = re.compile(r"^[\s*]*(\([a-z]{1,2}\)[\s*]*)+$|^\*+$", re.I)
SECTION_HEADING_ROWS = 12
QUARTER_HEADING = re.compile(
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\b.*quarter|^q[1-4]\b", re.I
)
# Only a row without a name of its own may borrow its meaning from the heading above it;
# under "Statement of Earnings" every row would otherwise count as earnings.
BARE_LABEL = r"(total|totals|consolidated)?\W*"
# A row called "Balance" is equity only in a statement of equity, not in a goodwill table.
EQUITY_IN_LABEL = r"equity|deficit|deficiency|capital|net assets|investment"
EQUITY_IN_TABLE = (
    r"stockholders|shareholders|shareowners|share owners|retained earnings"
    r"|accumulated deficit|treasury|partners|members"
)


class HtmlTables(HTMLParser):
    """Collect the text of each table cell; nested tables become separate tables."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.open_tables = []
        self.cell_parts = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.open_tables.append([])
        elif tag == "tr" and self.open_tables:
            self.open_tables[-1].append([])
        elif tag in ("td", "th") and self.open_tables:
            self.in_cell = True
            self.cell_parts = []
        elif tag == "br" and self.in_cell:
            self.cell_parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.open_tables and self.in_cell:
            text = " ".join("".join(self.cell_parts).split())
            if self.open_tables[-1]:
                self.open_tables[-1][-1].append(text)
            self.in_cell = False
        elif tag == "table" and self.open_tables:
            self.tables.append(self.open_tables.pop())

    def handle_data(self, data):
        if self.in_cell:
            self.cell_parts.append(data)


def clean_row(row):
    """Drop layout cells; a closing bracket in its own cell joins the previous number."""
    cells = []
    for text in row:
        text = text.replace("\xa0", " ").replace("​", "").strip()
        if text in SKIPPED_CELLS:
            continue
        if text.startswith(")") and cells:
            cells[-1] += ")"
            continue
        cells.append(text)
    return cells


def parse_tables(html):
    parser = HtmlTables()
    parser.feed(html)
    result = []
    for table in parser.tables:
        rows = [clean_row(row) for row in table]
        rows = [row for row in rows if row]
        if rows:
            result.append(rows)
    return result


def number(text):
    match = NUMBER.match(text.strip())
    if not match:
        return None
    try:
        value = Decimal(match.group("n").replace(",", ""))
    except InvalidOperation:
        return None
    return -value if match.group("neg") else value


def is_year_cell(text):
    return re.fullmatch(r"\s*(19|20)\d\d\s*(\(\w{1,2}\)|,|\*)*\s*", text) is not None


def period_token(text):
    """The date or the year a cell names, or None."""
    found = DATE.search(text)
    if found:
        month = MONTHS.index(found.group(1)[:3].lower()) + 1
        try:
            return date(int(found.group(3)), month, int(found.group(2)))
        except ValueError:
            return None
    found = YEAR.search(text)
    return int(found.group(1)) if found else None


def header_tokens(rows):
    """Period tokens of every cell of the nearest header row, and its index.

    A multi-cell row is a header only when a cell after the first holds a period;
    otherwise it is a label with dashes (e.g. "Common stock ... at December 31, 2024").
    """
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if any(number(cell) is not None and not is_year_cell(cell) for cell in row[1:]):
            continue
        tokens = [period_token(cell) for cell in row]
        if len(row) > 1 and not any(token is not None for token in tokens[1:]):
            continue
        if len(row) == 1 and len(row[0]) > 40:
            continue  # a sentence mentioning a year, not a column heading
        if not any(token is not None for token in tokens):
            continue
        return tokens, index
    return None, None


def align_columns(tokens, values, header_text):
    """Header token for each value column, or None when the layout is ambiguous."""
    # A header whose first cell has no period starts with the label column; extra
    # unlabelled cells (e.g. "Year Ended December 31") may precede the periods.
    if tokens[0] is not None:
        columns = list(tokens)
    else:
        columns = list(tokens[1:])
        while columns and columns[0] is None and len(columns) > len(values):
            columns.pop(0)
    if not columns:
        return None
    if len(columns) == len(values):
        return columns
    if len(columns) >= 2 and len(values) % len(columns) == 0:
        # Several values per period (e.g. amount and percent of revenue).
        group_size = len(values) // len(columns)
        return [columns[index // group_size] for index in range(len(values))]
    # Trailing change columns ("Percent Change", "$ Change") without a period header.
    extra = len(values) - len(columns)
    if 0 < extra <= 2 and re.search(r"change|%|increase|variance", header_text, re.I):
        return columns + [None] * extra
    return None


def duration_months(text):
    text = text.lower()
    for word, months in DURATIONS:
        if word in text:
            return months
    return None


def column_months(rows, header_index, position, value_count):
    """Duration of the value column from the nearest duration row above the header."""
    for row in reversed(rows[: header_index + 1]):
        durations = [duration_months(cell) for cell in row]
        durations = [months for months in durations if months is not None]
        if durations:
            if value_count % len(durations) == 0:
                return durations[position // (value_count // len(durations))]
            return durations[0]
    return 12


def label_years(task, period_end, header_years):
    """Year labels that name this period in a header of bare years.

    Years ending in January-March are named either by the calendar year they end in
    or by the one they start in. The convention is read from the filing itself: its
    latest column is the fiscal year that ended shortly before the filing date.
    """
    if period_end.month == 1 and period_end.day <= 10:
        return {period_end.year - 1}
    if period_end.month > 3:
        return {period_end.year}
    filed = date.fromisoformat(task["known_from"])
    own_end = date(filed.year, period_end.month, min(period_end.day, 28))
    if own_end.toordinal() > filed.toordinal() - 20:
        own_end = own_end.replace(year=own_end.year - 1)
    if not header_years:
        return set()
    latest_label = max(header_years)
    if latest_label == own_end.year:
        return {period_end.year}
    if latest_label == own_end.year - 1:
        return {period_end.year - 1}
    return set()


def label_index(cells):
    """Index of the row label: the first non-numeric cell (share counts may precede it)."""
    for index, cell in enumerate(cells[:-1]):
        if number(cell) is None:
            return index
    return None


def nearest_balance_day(rows):
    """The date of the last dated balance row, or None when the rows have no such row."""
    for row in reversed(rows):
        label = row[0].strip()
        is_balance_row = re.match(r"(balances?\b|beginning|ending)", label, re.I) or (
            len(label) <= 30 and DATE.match(label)
        )
        balance_day = period_token(label)
        if len(row) > 1 and is_balance_row and isinstance(balance_day, date):
            return balance_day
    return None


def under_quarter_heading(rows):
    """Whether a heading such as "First Quarter" stands between the header and the row."""
    for row in rows:
        if QUARTER_HEADING.search(row[0]) and all(
            number(cell) is None for cell in row[1:]
        ):
            return True
    return False


def period_fits(task, table, row_index, column_index, cells, previous_table=()):
    """How the cell's position shows the task's period, or None when it does not."""
    period_end = date.fromisoformat(task["period_end"])
    label_at = label_index(cells)
    label = cells[label_at] if label_at is not None else ""
    beginning = re.search(r"beginning|opening", label, re.I) is not None
    label_token = period_token(label)
    if isinstance(label_token, date) and not beginning:
        # A dated row (e.g. "Balance at December 31, 2022") defines its own period.
        return "row date" if abs((label_token - period_end).days) <= 7 else None
    if is_year_cell(label):
        # Rows named by a bare year; for January-March year ends the naming is ambiguous.
        fits = period_end.month > 3 and int(label.strip()[:4]) == period_end.year
        return "row year" if fits else None
    year_end = re.search(
        r"\b(?:year-end|year end|end of (?:fiscal )?year)\s*((?:19|20)\d\d)\b",
        label,
        re.I,
    )
    if year_end and not beginning:
        # "Balance at year-end 2024": the row is the balance at the end of that year.
        fits = period_end.month > 3 and int(year_end.group(1)) == period_end.year
        return "row year-end" if fits else None
    if label_at is None and number(cells[-1]) is None:
        return None  # a single label without values
    first_value_at = 0 if label_at is None else label_at + 1
    if column_index < first_value_at:
        return None
    # A total row may have no label of its own; its numbers are then all columns.
    values = [cell for cell in cells[first_value_at:] if not FOOTNOTE_CELL.match(cell)]
    marks_before = [
        cell for cell in cells[first_value_at:column_index] if FOOTNOTE_CELL.match(cell)
    ]
    position = column_index - first_value_at - len(marks_before)
    rows = table[:row_index]
    tokens, header_index = header_tokens(rows)
    # Dated rows below the header (statement of equity stacked by year) mean the header
    # columns are not periods: the row belongs to the year after the nearest balance.
    first_row_below_header = 0 if header_index is None else header_index + 1
    balance_day = nearest_balance_day(rows[first_row_below_header:])
    if balance_day is not None:
        if beginning:
            return None
        next_balance = balance_day.replace(
            year=balance_day.year + 1, day=min(balance_day.day, 28)
        )
        fits = abs((next_balance - period_end).days) <= 7
        return "stacked statement" if fits else None
    if not tokens and previous_table:
        # A statement split into two tables keeps its header in the first one;
        # accepted only when the header has exactly one period per value column.
        rows = list(previous_table) + rows
        tokens, header_index = header_tokens(rows)
        if tokens:
            periods = [token for token in tokens if token is not None]
            if len(periods) != len(values):
                return None
            if nearest_balance_day(rows[header_index + 1 :]) is not None:
                return None
    if not tokens or header_index is None:
        return None

    header_years = {token for token in tokens if isinstance(token, int)}
    header_rows = rows[max(0, header_index - 2) : header_index + 1]
    header_text = " ".join(" ".join(row) for row in header_rows)
    columns = align_columns(tokens, values, header_text)
    if columns is None:
        return None
    chosen = columns[position]
    # A year in the header does not make a quarter's column an annual figure.
    if task["metric"] != "equity":
        months = column_months(rows, header_index, position, len(values))
        if months < 12 or under_quarter_heading(rows[header_index + 1 :]):
            return None
    if beginning:
        # A beginning balance belongs to the day before the column period starts.
        months = column_months(rows, header_index, position, len(values))
        if isinstance(chosen, int):
            # Bare years only for annual columns; a quarter labelled "2020" starts in 2020.
            fits = months == 12 and chosen - 1 in label_years(
                task, period_end, header_years
            )
        elif isinstance(chosen, date):
            approximate_start = chosen.toordinal() - round(months * 30.44)
            fits = abs(approximate_start - period_end.toordinal()) <= 12
        else:
            fits = False
        return "column beginning" if fits else None
    if isinstance(chosen, date):
        return "column" if abs((chosen - period_end).days) <= 7 else None
    if isinstance(chosen, int):
        fits = chosen in label_years(task, period_end, header_years)
        return "column" if fits else None
    return None


def component_label(tag, metric):
    """The row label a component of a calculated value must have."""
    if "IncludingPortion" in tag:
        return ROW_LABELS[metric]
    if re.search("Noncontrolling|Minority", tag):
        return r"noncontrolling|non-controlling|minority"
    if "Discontinued" in tag:
        return r"discontinued"
    if "Continuing" in tag:
        return r"operat|continuing"
    if tag.startswith("InterestIncomeExpense"):
        return r"interest income"
    if tag.startswith("Noninterest"):
        return r"noninterest|non-interest|other|fee|total"
    if metric == "equity":
        return ROW_LABELS["equity"]
    return None


def section_heading(table, row_index):
    """Nearest row above that has a label but no values."""
    for row in reversed(table[max(0, row_index - SECTION_HEADING_ROWS) : row_index]):
        if len(row) == 1 or all(number(cell) is None for cell in row[1:]):
            if period_token(row[0]) is None:
                return row[0]
    return ""


def table_mentions_equity(table):
    """Whether a table is a statement of equity, judged by all its labels and headers."""
    text = " ".join(" ".join(row[:1]) for row in table)
    text += " ".join(" ".join(row) for row in table[:4])
    return re.search(EQUITY_IN_TABLE, text.lower()) is not None


class Filing:
    """The extracted tables of one filing, indexed by the numbers they contain."""

    def __init__(self, documents):
        self.documents = documents
        self.cells_by_value = defaultdict(list)
        for document_index, document in enumerate(documents):
            for table_index, table in enumerate(document["tables"]):
                for row_index, row in enumerate(table):
                    has_label = label_index(row) is not None
                    for column_index, cell in enumerate(row):
                        if column_index == 0 and len(row) > 1:
                            # The first cell is a label or the year naming the row,
                            # unless the whole row holds only numbers.
                            if has_label or is_year_cell(cell):
                                continue
                        value = number(cell)
                        if value is not None and value != 0:
                            position = (
                                document_index,
                                table_index,
                                row_index,
                                column_index,
                            )
                            self.cells_by_value[value].append(position)

    def previous_table(self, document_index, table_index):
        if table_index == 0:
            return ()
        return self.documents[document_index]["tables"][table_index - 1]

    def find(
        self,
        value,
        scales,
        task,
        label_pattern,
        excluded_pattern,
        context=False,
        bare_rows_only=False,
    ):
        """Best evidence for one value: (level, text); level 3 = value + label + period."""
        best = (0, "")
        for scale in scales:
            for position in self.cells_by_value.get(value / scale, []):
                document_index, table_index, row_index, column_index = position
                document = self.documents[document_index]
                table = document["tables"][table_index]
                cells = table[row_index]
                label_at = label_index(cells)
                label = cells[label_at].lower() if label_at is not None else ""
                if context and (not bare_rows_only or re.fullmatch(BARE_LABEL, label)):
                    # Sub-rows such as "Total" under a "Diluted" or "Revenues" heading
                    # take the heading as part of their label.
                    label = section_heading(table, row_index).lower() + " / " + label
                label_fits = label_pattern is None or re.search(label_pattern, label)
                if excluded_pattern and re.search(excluded_pattern, label):
                    label_fits = False
                if re.search(NOT_GAAP, label):
                    label_fits = False
                if (
                    label_fits
                    and label_pattern == ROW_LABELS["equity"]
                    and not re.search(EQUITY_IN_LABEL, label)
                    and not table_mentions_equity(table)
                ):
                    label_fits = False
                period = period_fits(
                    task,
                    table,
                    row_index,
                    column_index,
                    cells,
                    self.previous_table(document_index, table_index),
                )
                if label_fits and period:
                    level = FULL_MATCH
                elif period:
                    level = 1.5
                else:
                    level = 2 if label_fits else 1
                row_text = " | ".join(cells)[:160]
                evidence = f"{document['type']} T{table_index}: {row_text} [{period}]"
                if level > best[0]:
                    best = (level, evidence)
                if level == FULL_MATCH:
                    return best
        return best

    def rows_with_value(self, value, scales, task):
        """(document, table, row) of every occurrence of the value in its period."""
        rows = set()
        for scale in scales:
            for position in self.cells_by_value.get(value / scale, []):
                document_index, table_index, row_index, column_index = position
                table = self.documents[document_index]["tables"][table_index]
                previous = self.previous_table(document_index, table_index)
                cells = table[row_index]
                if period_fits(task, table, row_index, column_index, cells, previous):
                    rows.add((document_index, table_index, row_index))
        return rows


def check_eps(task, filing):
    candidates = [Decimal(task["reported_eps"] or task["value"])]
    if task["eps_basis"] == "basic":
        candidates.append(Decimal(task["value"]) * BERKSHIRE_CLASS_A_MULTIPLE)
    best = (0, "")
    for candidate in candidates:
        for context in (False, True):
            found = filing.find(
                candidate,
                ONE_SCALE,
                task,
                ROW_LABELS["eps_diluted"],
                EXCLUDED_ROW_LABELS["eps_diluted"],
                context,
            )
            if found[0] > best[0]:
                best = found
    if best[0] == FULL_MATCH:
        return "pass_direct", best[1]
    return f"queue_level_{best[0]}", best[1]


def check_components(task, filing, direct):
    """A calculated value: every component must stand in its own row and period."""
    evidence = []
    weak = []
    for source in task["sources"]:
        pattern = component_label(source["tag"], task["metric"])
        value = Decimal(source["value"])
        found = filing.find(value, AMOUNT_SCALES, task, pattern, None)
        if found[0] != FULL_MATCH:
            found = filing.find(value, AMOUNT_SCALES, task, pattern, None, True)
        evidence.append(f"{source['tag']}: {found[1]}")
        if found[0] != FULL_MATCH:
            weak.append((source, found))
    if not weak:
        return "pass_components", " || ".join(evidence)
    # Components shown side by side in one dated row (statement of equity columns).
    shared_rows = None
    for source in task["sources"]:
        rows = filing.rows_with_value(Decimal(source["value"]), AMOUNT_SCALES, task)
        shared_rows = rows if shared_rows is None else shared_rows & rows
    if shared_rows:
        return "pass_components_same_row", " || ".join(evidence)
    source, found = weak[0]
    return (
        f"queue_component_level_{found[0]}",
        f"{source['tag']}: {found[1]} || direct: {direct[1]}",
    )


def check_version(task, filing):
    """Return (verdict, evidence); a verdict starting with "pass" needs no review."""
    if filing is None:
        return "queue_no_document", ""
    if not filing.documents:
        return "queue_no_primary_document", ""
    metric = task["metric"]
    if metric == "eps_diluted":
        return check_eps(task, filing)
    value = Decimal(task["value"])
    pattern = ROW_LABELS[metric]
    excluded = EXCLUDED_ROW_LABELS.get(metric)
    direct = filing.find(value, AMOUNT_SCALES, task, pattern, excluded)
    if direct[0] != FULL_MATCH:
        under_heading = filing.find(
            value, AMOUNT_SCALES, task, pattern, excluded, True, bare_rows_only=True
        )
        if under_heading[0] > direct[0]:
            direct = under_heading
    if direct[0] == FULL_MATCH:
        return "pass_direct", direct[1]
    if task["calculation"] and len(task["sources"]) > 1:
        return check_components(task, filing, direct)
    return f"queue_level_{direct[0]}", direct[1]


def extract_path(accession):
    return EXTRACTS / f"{accession}.json.gz"


def load_filing(accession):
    """Read a filing's extract; None when it was never downloaded."""
    path = extract_path(accession)
    if not path.exists():
        return None
    payload = json.loads(gzip.decompress(path.read_bytes()))
    return Filing(payload["docs"])


def filing_documents(cik, accession, form):
    """The filing's own form document and its annual report exhibit, from its index page."""
    folder = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
    )
    index_url = folder + accession + "-index.htm"
    index_page = fetch(index_url, user_agent(), json_response=False)
    index_text = index_page.decode("utf-8", "replace")
    found = []
    for table_row in re.findall(r"<tr[^>]*>(.*?)</tr>", index_text, re.S | re.I):
        cells = [
            unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", table_row, re.S | re.I)
        ]
        link = re.search(r'href="([^"]+)"', table_row)
        if len(cells) < 4 or not link:
            continue
        kind = cells[3].upper()
        if kind != form and not kind.startswith("EX-13"):
            continue
        href = link.group(1).replace("/ix?doc=", "")
        url = "https://www.sec.gov" + href if href.startswith("/") else folder + href
        if url.lower().endswith((".htm", ".html", ".txt")):
            found.append((kind, url))
    return found


def fetch_extract(cik, accession, form):
    """Download a filing's documents and keep only their tables; the HTML is not stored."""
    documents = []
    for kind, url in filing_documents(cik, accession, form):
        body = fetch(url, user_agent(), json_response=False)
        documents.append(
            {
                "type": kind,
                "url": url,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
                "tables": parse_tables(body.decode("utf-8", "replace")),
            }
        )
    payload = json.dumps({"accession": accession, "docs": documents}).encode()
    EXTRACTS.mkdir(parents=True, exist_ok=True)
    target = extract_path(accession)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(gzip.compress(payload))
    temporary.replace(target)


def version_tasks(connection):
    """Every served version with what its check needs; newest filings are not preferred."""
    from sqlalchemy import text

    rows = (
        connection.execute(
            text("""
            SELECT co.ticker, co.cik, v.metric, v.confirmation, v.form, v.accession,
                   v.known_from, v.period_start, v.period_end, v.value, v.details
            FROM annual_metric_versions v JOIN companies co ON co.id = v.company_id
            WHERE v.status = 'ready'
            ORDER BY co.ticker, v.metric, v.period_end, v.known_from
        """)
        )
        .mappings()
        .all()
    )
    tasks = []
    for row in rows:
        details = row["details"]
        sources = details["selected_sources"]
        own_sources = [
            source for source in sources if source["accession"] == row["accession"]
        ]
        cik = (own_sources or sources)[0]["source_cik"] if sources else row["cik"]
        share_basis = details.get("eps_share_basis") or {}
        tasks.append(
            {
                "ticker": row["ticker"],
                "cik": int(cik),
                "metric": row["metric"],
                "confirmation": row["confirmation"],
                "form": row["form"],
                "accession": row["accession"],
                "known_from": str(row["known_from"]),
                "period_start": str(row["period_start"]),
                "period_end": str(row["period_end"]),
                "value": str(row["value"]),
                "calculation": details.get("calculation"),
                "eps_basis": details.get("eps_basis"),
                "reported_eps": share_basis.get("reported_value"),
                "sources": [
                    {"tag": source["tag"], "value": source["value"]}
                    for source in sources
                ],
            }
        )
    return tasks


def reported_value(task):
    """The value as the filing states it: EPS before split normalisation."""
    return Decimal(task["reported_eps"] or task["value"])


def cell_value_key(task):
    return (
        task["ticker"],
        task["metric"],
        task["period_start"],
        task["period_end"],
        reported_value(task),
    )


def confirmed_elsewhere(queued_tasks, passed_tasks):
    """Queued versions whose exact value passed in another filing, for the same period.

    A statement can resist reading in one filing (a 10-K/A without statements, a table
    without a period header) although another report shows the same number in its place.
    The value is then confirmed, though this filing's own table was not read.
    """
    passed_keys = {cell_value_key(task) for task in passed_tasks}
    return [task for task in queued_tasks if cell_value_key(task) in passed_keys]


def review_key(item):
    value = Decimal(item["value"])
    return (
        item["ticker"],
        item["metric"],
        item["period_end"],
        item["accession"],
        value,
    )


def separate_reviewed(queue, reviews):
    """Split the queue into reviewed and not; report reviews no queue item matches.

    A review pins the value it saw, so a changed value is unreviewed again. A review
    that matches nothing means the automatic check now passes or the version is gone.
    """
    reviewed_keys = {review_key(review) for review in reviews}
    queue_keys = {review_key(item) for item in queue}
    unreviewed = [item for item in queue if review_key(item) not in reviewed_keys]
    stale = [review for review in reviews if review_key(review) not in queue_keys]
    return unreviewed, stale


def read_reviews():
    document = read_json(REVIEWS)
    if document["version"] != 1:
        raise ValueError("Unsupported document check reviews version")
    return document["reviews"]


def group_by_filing(tasks):
    groups = defaultdict(list)
    for task in tasks:
        groups[task["accession"]].append(task)
    return groups


def check_documents(fetch_missing=False):
    """Check every served version against its own filing; report what needs a person."""
    from app.database import engine

    with engine.connect() as connection:
        tasks = version_tasks(connection)
    groups = group_by_filing(tasks)
    fetched = 0
    if fetch_missing:
        for accession, group in groups.items():
            if not extract_path(accession).exists():
                first = group[0]
                fetch_extract(first["cik"], accession, first["form"])
                fetched += 1

    columns = (
        "ticker",
        "metric",
        "confirmation",
        "form",
        "period_end",
        "known_from",
        "accession",
        "value",
    )
    outcomes = []
    for accession, group in groups.items():
        filing = load_filing(accession)
        for task in group:
            verdict, evidence = check_version(task, filing)
            outcomes.append((task, verdict, evidence))
    passed_tasks = [task for task, verdict, _ in outcomes if verdict.startswith("pass")]
    queued_tasks = [
        task for task, verdict, _ in outcomes if not verdict.startswith("pass")
    ]
    elsewhere = {id(task) for task in confirmed_elsewhere(queued_tasks, passed_tasks)}

    verdicts = Counter()
    queue = []
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([*columns, "verdict", "evidence"])
        for task, verdict, evidence in outcomes:
            if id(task) in elsewhere:
                verdict = "pass_same_value_in_other_filing"
            verdicts[verdict] += 1
            writer.writerow([*(task[column] for column in columns), verdict, evidence])
            if not verdict.startswith("pass"):
                queue.append(
                    {
                        **{column: task[column] for column in columns},
                        "verdict": verdict,
                        "evidence": evidence,
                    }
                )

    reviews = read_reviews()
    unreviewed, stale = separate_reviewed(queue, reviews)
    result = {
        "status": "verified" if not unreviewed and not stale else "needs_review",
        "versions": len(tasks),
        "filings": len(groups),
        "extracts_fetched": fetched,
        "verdicts": dict(sorted(verdicts.items())),
        "reviewed": len(queue) - len(unreviewed),
        "unreviewed": len(unreviewed),
        "stale_reviews": len(stale),
    }
    write_json(REPORT, {**result, "queue": unreviewed, "stale": stale})
    return result


def shifted_task(task, kind):
    """A task made wrong on purpose: one year earlier, or the value off by 1%."""
    wrong = dict(task)
    if kind == "wrong_year":
        for key in ("period_start", "period_end"):
            day = date.fromisoformat(task[key])
            wrong[key] = str(day.replace(year=day.year - 1, day=min(day.day, 28)))
        return wrong
    factor = Decimal("1.01")
    wrong["value"] = str(Decimal(task["value"]) * factor)
    if task["reported_eps"]:
        wrong["reported_eps"] = str(Decimal(task["reported_eps"]) * factor)
    wrong["sources"] = [
        {**source, "value": str(Decimal(source["value"]) * factor)}
        for source in task["sources"]
    ]
    return wrong


def wrong_rows_of_filing(filing, metric):
    """Positions of the rows of a filing that report something else than the metric."""
    pattern = WRONG_ROWS[metric]
    positions = []
    for document_index, document in enumerate(filing.documents):
        for table_index, table in enumerate(document["tables"]):
            for row_index, cells in enumerate(table):
                label_at = label_index(cells)
                if label_at is None:
                    continue
                if re.search(pattern, cells[label_at].lower()):
                    positions.append((document_index, table_index, row_index, label_at))
    return positions


def wrong_row_values(task, filing, positions):
    """Numbers of the task's period in the given wrong rows."""
    values = set()
    for document_index, table_index, row_index, label_at in positions:
        table = filing.documents[document_index]["tables"][table_index]
        previous = filing.previous_table(document_index, table_index)
        cells = table[row_index]
        for column_index in range(label_at + 1, len(cells)):
            value = number(cells[column_index])
            if value is None or value == 0:
                continue
            if period_fits(task, table, row_index, column_index, cells, previous):
                values.add(value)
    return values


def wrong_row_task(task, value):
    wrong = dict(task)
    wrong["value"] = str(value)
    wrong["reported_eps"] = None
    wrong["calculation"] = None
    wrong["sources"] = []
    return wrong


def control_documents():
    """Negative control: plant wrong years, values and rows and count what still passes."""
    from app.database import engine

    with engine.connect() as connection:
        tasks = version_tasks(connection)
    counts = Counter()
    passed_examples = defaultdict(list)
    shown = Counter()
    for accession, group in group_by_filing(tasks).items():
        filing = load_filing(accession)
        if filing is None or not filing.documents:
            continue
        wrong_rows = {
            metric: wrong_rows_of_filing(filing, metric)
            for metric in {task["metric"] for task in group}
        }
        for task in group:
            planted = [
                ("wrong_year", shifted_task(task, "wrong_year")),
                ("wrong_value", shifted_task(task, "wrong_value")),
            ]
            true_value = reported_value(task)
            true_cells = {true_value / scale for scale in AMOUNT_SCALES}
            for value in wrong_row_values(task, filing, wrong_rows[task["metric"]]):
                if value in true_cells:
                    continue
                # Percents and small sub-rows are not amounts a wrong row would give.
                is_amount = task["metric"] != "eps_diluted"
                if is_amount and abs(value) < SMALLEST_PLANTED_AMOUNT:
                    continue
                planted.append(("wrong_row", wrong_row_task(task, value)))
            for kind, wrong in planted:
                verdict, evidence = check_version(wrong, filing)
                passed = verdict.startswith("pass")
                counts[f"{kind}_tried"] += 1
                counts[f"{kind}_{task['metric']}_tried"] += 1
                if passed:
                    counts[f"{kind}_passed"] += 1
                    counts[f"{kind}_{task['metric']}_passed"] += 1
                    shown[(kind, task["metric"])] += 1
                    if shown[(kind, task["metric"])] <= EXAMPLES_PER_METRIC:
                        passed_examples[kind].append(
                            {
                                "ticker": task["ticker"],
                                "metric": task["metric"],
                                "period_end": wrong["period_end"],
                                "value": wrong["value"],
                                "evidence": evidence,
                            }
                        )
    result = {"status": "completed", **dict(sorted(counts.items()))}
    write_json(CONTROL_REPORT, {**result, "passed_examples": passed_examples})
    return result
