"""The screener grid comparison (K20): which cells changed between two runs."""

import unittest
from datetime import date

from app.data_pipeline.screener_grid import (
    flat_row,
    grid_comparison,
    grid_differences,
    month_ends,
)


class MonthEndTests(unittest.TestCase):
    def test_every_month_end_of_the_window_is_listed_once(self):
        """The screener window gives 118 month ends, the leap day of 2020 included;
        a date in the middle of a month still lists that month's end."""
        days = month_ends(date(2016, 3, 31), date(2025, 12, 31))

        self.assertEqual(len(days), 118)
        self.assertEqual(days[0], date(2016, 3, 31))
        self.assertEqual(days[-1], date(2025, 12, 31))
        self.assertIn(date(2020, 2, 29), days)
        self.assertEqual(
            month_ends(date(2024, 1, 15), date(2024, 2, 1)),
            [
                date(2024, 1, 31),
                date(2024, 2, 29),
            ],
        )


class FlatRowTests(unittest.TestCase):
    def test_ratios_and_spin_offs_become_single_fields(self):
        """Each ratio is its own field, so a difference names the ratio that changed."""
        latest = {
            "period_end": "2023-12-31",
            "spin_offs": [{"event_date": "2024-04-01", "separated_fraction": "0.2"}],
            "ratios": {"pe": "20", "pb": None},
        }

        row = flat_row(latest)

        self.assertEqual(
            row,
            {
                "period_end": "2023-12-31",
                "spin_offs": "2024-04-01:0.2",
                "pe": "20",
                "pb": None,
            },
        )

    def test_company_without_a_report_has_one_empty_field(self):
        self.assertEqual(flat_row(None), {"latest": None})


class DifferenceTests(unittest.TestCase):
    def test_equal_grids_have_no_differences(self):
        grid = {"2024-03-31": {"AAA": {"pe": "20"}}}

        self.assertEqual(grid_differences(grid, grid), [])

    def test_each_changed_cell_is_listed_with_both_values(self):
        """A ratio that becomes empty is a change too, never ignored as missing."""
        saved = {"2024-03-31": {"AAA": {"pe": "20", "pb": "4"}, "BBB": {"pe": "8"}}}
        current = {"2024-03-31": {"AAA": {"pe": "21", "pb": None}, "BBB": {"pe": "8"}}}

        differences = grid_differences(saved, current)

        self.assertEqual(
            differences,
            [
                {
                    "date": "2024-03-31",
                    "ticker": "AAA",
                    "field": "pb",
                    "saved": "4",
                    "current": None,
                },
                {
                    "date": "2024-03-31",
                    "ticker": "AAA",
                    "field": "pe",
                    "saved": "20",
                    "current": "21",
                },
            ],
        )

    def test_a_company_missing_on_one_side_is_a_difference(self):
        """The whole row differs; its fields are not compared one by one."""
        saved = {"2024-03-31": {"AAA": {"pe": "20"}}}
        current = {"2024-03-31": {"AAA": {"pe": "20"}, "NEW": {"pe": "5"}}}

        differences = grid_differences(saved, current)

        self.assertEqual(
            differences,
            [
                {
                    "date": "2024-03-31",
                    "ticker": "NEW",
                    "field": "row",
                    "saved": False,
                    "current": True,
                }
            ],
        )


class FieldChangeTests(unittest.TestCase):
    def test_new_field_is_reported_once_with_its_values(self):
        """N129: a new response field (share_issue, 17.11.2026) is summarised once with
        its value counts instead of as a difference in every cell, while a real change
        of another field in the same run is still listed."""
        saved = {
            "2024-03-31": {"AAA": {"pe": "20"}, "BBB": {"pe": "8"}},
            "2024-04-30": {"AAA": {"pe": "20"}, "EMPTY": {"latest": None}},
        }
        current = {
            "2024-03-31": {
                "AAA": {"pe": "20", "share_issue": True},
                "BBB": {"pe": "9", "share_issue": False},
            },
            "2024-04-30": {
                "AAA": {"pe": "20", "share_issue": True},
                "EMPTY": {"latest": None},
            },
        }

        comparison = grid_comparison(saved, current)

        self.assertEqual(
            comparison["new_fields"],
            {
                "share_issue": {
                    "cells": 3,
                    "distinct_values": 2,
                    "most_common": [("True", 2), ("False", 1)],
                }
            },
        )
        self.assertEqual(comparison["removed_fields"], {})
        self.assertEqual(
            comparison["differences"],
            [
                {
                    "date": "2024-03-31",
                    "ticker": "BBB",
                    "field": "pe",
                    "saved": "8",
                    "current": "9",
                }
            ],
        )

    def test_removed_field_is_reported_once_from_the_saved_grid(self):
        saved = {"2024-03-31": {"AAA": {"pe": "20", "old": "x"}}}
        current = {"2024-03-31": {"AAA": {"pe": "20"}}}

        comparison = grid_comparison(saved, current)

        self.assertEqual(list(comparison["removed_fields"]), ["old"])
        self.assertEqual(comparison["removed_fields"]["old"]["cells"], 1)
        self.assertEqual(comparison["new_fields"], {})
        self.assertEqual(comparison["differences"], [])

    def test_company_losing_its_report_still_differs_in_every_field(self):
        """An empty row is not a field change: a side whose rows are all empty does not
        make every field look removed, so each lost value is still listed."""
        saved = {"2024-03-31": {"AAA": {"pe": "20"}}}
        current = {"2024-03-31": {"AAA": {"latest": None}}}

        comparison = grid_comparison(saved, current)

        self.assertEqual(comparison["new_fields"], {})
        fields = [difference["field"] for difference in comparison["differences"]]
        self.assertEqual(fields, ["pe"])


if __name__ == "__main__":
    unittest.main()
