"""Invariant checks: relations between served values on every day they were in force."""

import json
import unittest
from datetime import date
from decimal import Decimal

from app.data_pipeline.identity_checks import separate_reviewed_flags
from app.data_pipeline.invariant_checks import (
    REVIEWS,
    Version,
    all_flags,
    in_force,
    same_period_violations,
    year_over_year_violations,
)

YEAR_2016 = (date(2016, 1, 1), date(2016, 12, 31))
YEAR_2017 = (date(2017, 1, 1), date(2017, 12, 31))


def ready(day, value):
    return Version(day, "ready", Decimal(value))


def checks(flags):
    return sorted((flag["check"], flag["period_end"]) for flag in flags)


class InForceTests(unittest.TestCase):
    def test_the_latest_version_known_on_the_day_is_served(self):
        """Before the first report nothing is known; a later report replaces the value;
        an unchecked version serves no value, as the as_of view shows none."""
        versions = [
            ready(date(2018, 2, 28), "100"),
            ready(date(2019, 3, 1), "110"),
            Version(date(2020, 3, 1), "needs_review", Decimal("90")),
        ]

        self.assertIsNone(in_force(versions, date(2018, 2, 27)))
        self.assertEqual(in_force(versions, date(2018, 2, 28)), Decimal("100"))
        self.assertEqual(in_force(versions, date(2019, 12, 31)), Decimal("110"))
        self.assertIsNone(in_force(versions, date(2020, 6, 30)))


class SamePeriodTests(unittest.TestCase):
    def test_a_consistent_period_has_no_flags(self):
        """Equity below assets, capex and both incomes below revenue."""
        values = {
            "revenue": Decimal(1000),
            "net_income": Decimal(100),
            "operating_income": Decimal(150),
            "capex": Decimal(50),
            "equity": Decimal(400),
            "assets": Decimal(900),
        }

        self.assertEqual(same_period_violations(values), [])

    def test_impossible_and_unusual_values_are_flagged_with_their_size(self):
        """Equity 1.5 times assets is impossible; a negative capex has a wrong sign; net
        income 20% above revenue is unusual and needs an event that explains it."""
        values = {
            "revenue": Decimal(1000),
            "net_income": Decimal(1200),
            "capex": Decimal(-30),
            "equity": Decimal(1500),
            "assets": Decimal(1000),
        }

        self.assertEqual(
            sorted(same_period_violations(values)),
            [
                ("equity_over_assets", Decimal("50.0")),
                ("negative_capex", Decimal(100)),
                ("net_income_over_revenue", Decimal("20.0")),
            ],
        )

    def test_without_positive_revenue_only_the_balance_and_sign_are_tested(self):
        """A ratio to a missing or negative revenue says nothing."""
        values = {
            "revenue": Decimal(-5),
            "net_income": Decimal(10),
            "capex": Decimal(3),
        }

        self.assertEqual(same_period_violations(values), [])


class YearOverYearTests(unittest.TestCase):
    def test_normal_changes_pass_and_large_ones_are_flagged_both_ways(self):
        """Buybacks move shares by a few percent; a count in thousands is 1000 times
        smaller; assets falling to a third (a deconsolidation) also flag."""
        normal = year_over_year_violations(
            {"shares_diluted": Decimal(1000), "assets": Decimal(500)},
            {"shares_diluted": Decimal(960), "assets": Decimal(560)},
        )
        scale_error = year_over_year_violations(
            {"shares_diluted": Decimal(1000)}, {"shares_diluted": Decimal(1)}
        )
        deconsolidation = year_over_year_violations(
            {"assets": Decimal(900)}, {"assets": Decimal(200)}
        )

        self.assertEqual(normal, [])
        self.assertEqual(scale_error, [("shares_change", Decimal("99900.0"))])
        self.assertEqual(deconsolidation, [("assets_change", Decimal("350.0"))])

    def test_share_change_flags_only_above_one_and_a_half_times(self):
        """K19: exactly 1.5 times passes, a little more is flagged (CI 2018 at 1.52)."""
        at_limit = year_over_year_violations(
            {"shares_diluted": Decimal(1000)}, {"shares_diluted": Decimal(1500)}
        )
        above_limit = year_over_year_violations(
            {"shares_diluted": Decimal(1000)}, {"shares_diluted": Decimal(1520)}
        )

        self.assertEqual(at_limit, [])
        self.assertEqual(above_limit, [("shares_change", Decimal("52.0"))])


class HistoryTests(unittest.TestCase):
    def test_a_wrong_value_that_lived_only_in_the_history_is_found(self):
        """W29: Linde's 2017 assets were 9.2 million (the empty new company) from
        09.11.2018 until a later report restored 20,436 million. The latest values are
        consistent, so only a check of every day finds it, from the day it appeared."""
        cells = {
            "LIN": {
                YEAR_2017: {
                    "equity": [ready(date(2018, 2, 28), "5900000000")],
                    "assets": [
                        ready(date(2018, 2, 28), "20436000000"),
                        ready(date(2018, 11, 9), "9200000"),
                        ready(date(2019, 3, 18), "20436000000"),
                    ],
                }
            }
        }

        flags = all_flags(cells)

        self.assertEqual(checks(flags), [("equity_over_assets", "2017-12-31")])
        self.assertEqual(flags[0]["first_day"], "2018-11-09")

    def test_year_over_year_flags_use_values_known_on_the_same_day(self):
        """2017 shares in thousands until a corrected report; the year before is known
        from its own report. One flag for the period, with the largest deviation."""
        cells = {
            "TEST": {
                YEAR_2016: {"shares_diluted": [ready(date(2017, 2, 1), "1000000")]},
                YEAR_2017: {
                    "shares_diluted": [
                        ready(date(2018, 2, 1), "1000"),
                        ready(date(2018, 5, 1), "990000"),
                    ]
                },
            }
        }

        flags = all_flags(cells)

        self.assertEqual(checks(flags), [("shares_change", "2017-12-31")])
        self.assertEqual(flags[0]["first_day"], "2018-02-01")
        self.assertEqual(flags[0]["deviation_percent"], "99900.0")


class ReviewsFileTests(unittest.TestCase):
    def test_reviews_explain_only_unusual_values_and_name_an_event(self):
        """Impossible values are data errors, so no review may explain them away."""
        document = json.loads(REVIEWS.read_text(encoding="utf-8"))

        for review in document["reviews"]:
            with self.subTest(review=(review["ticker"], review["check"])):
                self.assertNotIn(
                    review["check"], ("equity_over_assets", "negative_capex")
                )
                self.assertTrue(review["note"])

    def test_a_larger_deviation_than_the_review_flags_again(self):
        """The review pins the deviation it explained, as in the identity checks."""
        review = {
            "ticker": "RCL",
            "check": "capex_over_revenue",
            "from_year": 2021,
            "to_year": 2021,
            "max_deviation_percent": "45.6",
        }
        covered = {"ticker": "RCL", "check": "capex_over_revenue", "year": 2021}

        explained, _ = separate_reviewed_flags(
            [{**covered, "deviation_percent": "45.6"}], [review]
        )
        larger, _ = separate_reviewed_flags(
            [{**covered, "deviation_percent": "60.0"}], [review]
        )

        self.assertEqual(explained, [])
        self.assertEqual(len(larger), 1)


if __name__ == "__main__":
    unittest.main()
