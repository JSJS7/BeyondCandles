"""Save the screener of every month end and compare a later run with it (K20).

A change of data or rules should change only the cells it is meant to change. The other
controls check single values or four reference cases; this one sees every company on
every month end of the window, so a side effect anywhere in it shows up as a difference.
"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone

from app.data_pipeline.source_files import ROOT, read_json, write_json

GRID = ROOT / "data/processed/screener-grid.json"
DIFFERENCES = ROOT / "data/processed/screener-grid-differences.json"
# Enough to read in the command output; the full list is in the differences file.
SHOWN_DIFFERENCES = 30


def month_ends(first, last):
    """Every last day of a month from the month of `first` to the month of `last`."""
    days = []
    year = first.year
    month = first.month
    while (year, month) <= (last.year, last.month):
        if month == 12:
            first_of_next = date(year + 1, 1, 1)
        else:
            first_of_next = date(year, month + 1, 1)
        days.append(first_of_next - timedelta(days=1))
        year = first_of_next.year
        month = first_of_next.month
    return days


def flat_row(latest):
    """One company's screener row as {field: text}; nested parts become one field each."""
    if latest is None:
        return {"latest": None}
    fields = {}
    for name, value in latest.items():
        if name == "ratios":
            for ratio, ratio_value in value.items():
                fields[ratio] = ratio_value
        elif name == "spin_offs":
            events = [
                f"{event['event_date']}:{event['separated_fraction']}"
                for event in value
            ]
            fields[name] = ";".join(events)
        else:
            fields[name] = value
    return fields


def current_grid(connection):
    """{valuation date: {ticker: flat row}} from the screener's own code."""
    from app.screener import (
        EARLIEST_VALUATION_DATE,
        LATEST_VALUATION_DATE,
        get_screener,
    )

    grid = {}
    for day in month_ends(EARLIEST_VALUATION_DATE, LATEST_VALUATION_DATE):
        response = get_screener(connection, day).model_dump(mode="json")
        rows = {}
        for row in response["rows"]:
            rows[row["ticker"]] = flat_row(row["latest"])
        grid[day.isoformat()] = rows
    return grid


def grid_differences(saved, current):
    """Every cell that differs, as {date, ticker, field, saved, current}.

    A date or a company missing on one side is a difference of the field "row".
    """
    differences = []
    for day in sorted(set(saved) | set(current)):
        saved_rows = saved.get(day, {})
        current_rows = current.get(day, {})
        for ticker in sorted(set(saved_rows) | set(current_rows)):
            if ticker not in saved_rows or ticker not in current_rows:
                differences.append(
                    {
                        "date": day,
                        "ticker": ticker,
                        "field": "row",
                        "saved": ticker in saved_rows,
                        "current": ticker in current_rows,
                    }
                )
                continue
            saved_row = saved_rows[ticker]
            current_row = current_rows[ticker]
            for field in sorted(set(saved_row) | set(current_row)):
                saved_value = saved_row.get(field)
                current_value = current_row.get(field)
                if saved_value != current_value:
                    differences.append(
                        {
                            "date": day,
                            "ticker": ticker,
                            "field": field,
                            "saved": saved_value,
                            "current": current_value,
                        }
                    )
    return differences


def read_grid():
    from app.database import engine

    with engine.connect() as connection:
        return current_grid(connection)


def save_screener_grid():
    """Store today's grid as the reference for the next comparison."""
    grid = read_grid()
    rows = sum(len(rows) for rows in grid.values())
    document = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dates": len(grid),
        "rows": rows,
        "grid": grid,
    }
    write_json(GRID, document)
    return {
        "status": "saved",
        "dates": len(grid),
        "rows": rows,
        "file": GRID.relative_to(ROOT).as_posix(),
    }


def compare_screener_grid():
    """Compare today's grid with the saved one; the reference file is not changed."""
    if not GRID.exists():
        raise ValueError("No saved screener grid; run save-screener-grid first")
    saved = read_json(GRID)
    differences = grid_differences(saved["grid"], read_grid())
    write_json(DIFFERENCES, {"saved_at": saved["saved_at"], "differences": differences})
    by_field = Counter(difference["field"] for difference in differences)
    by_ticker = Counter(difference["ticker"] for difference in differences)
    return {
        "status": "changed" if differences else "unchanged",
        "saved_at": saved["saved_at"],
        "differences": len(differences),
        "by_field": dict(by_field.most_common()),
        "by_ticker": dict(by_ticker.most_common()),
        "first_differences": differences[:SHOWN_DIFFERENCES],
        "file": DIFFERENCES.relative_to(ROOT).as_posix(),
    }
