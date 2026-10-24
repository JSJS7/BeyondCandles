"""Regression tests for reviewed price corrections, independent of network access."""

import unittest
from decimal import Decimal

from app.data_pipeline.price_adjustments import (
    apply_adjustments,
    find_dividend_coverage_gap,
    scan_adjustment_factors,
)


class PriceAdjustmentTests(unittest.TestCase):
    def test_avgo_dividend_uses_the_same_split_basis_as_close(self):
        """Use $0.325, not $3.25, against already 10-for-1 adjusted prices."""
        rows = [
            {
                "date": "2020-03-19",
                "close": "19.44499969482422",
                "adjusted_close": "10.304689407348633",
            },
            {
                "date": "2020-03-20",
                "close": "19.222000122070312",
                "adjusted_close": "12.230733871459961",
            },
        ]
        rule = {
            "method": "cash_dividends",
            "dividends": [
                {"ex_date": "2020-03-20", "amount": "3.25", "split_divisor": "10"},
            ],
        }

        corrected = apply_adjustments(rows, rule)
        change = (corrected["2020-03-20"] / corrected["2020-03-19"] - 1) * 100

        self.assertAlmostEqual(float(change), 0.533473, places=5)
        self.assertEqual(corrected["2020-03-19"], Decimal("19.119999694824220000"))
        self.assertEqual(corrected["2020-03-20"], Decimal(rows[1]["close"]))

    def test_future_dividends_do_not_enter_the_frozen_period(self):
        """Reject an action outside the supplied history instead of silently applying it."""
        rows = [
            {"date": "2024-12-19", "close": "100", "adjusted_close": "100"},
            {"date": "2024-12-20", "close": "102", "adjusted_close": "102"},
        ]
        rule = {
            "method": "cash_dividends",
            "dividends": [
                {"ex_date": "2024-12-23", "amount": "1", "split_divisor": "1"},
            ],
        }

        with self.assertRaisesRegex(ValueError, "Missing ex-dividend session"):
            apply_adjustments(rows, rule)

    def test_dhr_duplicate_distribution_does_not_create_a_false_return(self):
        """Remove the extra unscaled $24.56 adjustment while retaining the Close series."""
        rows = [
            {
                "date": "2016-07-01",
                "close": "68.77120208740234",
                "adjusted_close": "42.501182556152344",
            },
            {
                "date": "2016-07-05",
                "close": "71.27659606933594",
                "adjusted_close": "68.5197525024414",
            },
        ]
        rule = {
            "method": "remove_duplicate_distribution",
            "ex_date": "2016-07-05",
            "amount": "24.56",
        }

        corrected = apply_adjustments(rows, rule)
        change = (corrected["2016-07-05"] / corrected["2016-07-01"] - 1) * 100

        self.assertAlmostEqual(float(change), 3.643086, places=4)
        self.assertEqual(corrected["2016-07-05"], Decimal(rows[1]["adjusted_close"]))

    def test_invalid_actions_cannot_change_prices(self):
        """Reject duplicate dates, nonpositive cash and cash at least as large as Close."""
        rows = [
            {"date": "2020-01-02", "close": "10", "adjusted_close": "8"},
            {"date": "2020-01-03", "close": "11", "adjusted_close": "11"},
        ]
        for amounts in (("0",), ("-1",), ("10",), ("NaN",), ("1", "2")):
            rule = {
                "method": "cash_dividends",
                "dividends": [
                    {"ex_date": "2020-01-03", "amount": amount, "split_divisor": "1"}
                    for amount in amounts
                ],
            }
            with self.subTest(amounts=amounts), self.assertRaises(ValueError):
                apply_adjustments(rows, rule)

    def test_no_dividend_is_applied_twice_and_rows_are_not_mutated(self):
        """Rebuild from Close each time; apply each action strictly before its ex-date."""
        rows = [
            {"date": "2020-01-02", "close": "10", "adjusted_close": "1"},
            {"date": "2020-01-03", "close": "10", "adjusted_close": "2"},
            {"date": "2020-01-06", "close": "10", "adjusted_close": "3"},
        ]
        rule = {
            "method": "cash_dividends",
            "dividends": [
                {"ex_date": "2020-01-03", "amount": "1", "split_divisor": "1"},
                {"ex_date": "2020-01-06", "amount": "2", "split_divisor": "1"},
            ],
        }

        corrected = apply_adjustments(rows, rule)

        self.assertEqual(
            list(corrected.values()), [Decimal("7.2"), Decimal("8"), Decimal("10")]
        )
        self.assertEqual(corrected, apply_adjustments(rows, rule))
        self.assertEqual(rows[0]["adjusted_close"], "1")

    def test_reviewed_avgo_actions_correct_duplicate_and_missing_cash(self):
        """Keep issuer-announced common dividends, including actions absent from Kaggle."""
        from app.data_pipeline.source_files import MANIFEST, ROOT, read_json

        active = read_json(MANIFEST)
        policy = read_json(ROOT / active["reviews"]["AVGO"]["rules_file"])
        dividends = policy["companies"]["AVGO"]["dividends"]
        by_date = {item["ex_date"]: item for item in dividends}

        # Broadcom's 2018 release declares $1.75, not the vendor-implied $3.50.
        self.assertEqual(Decimal(by_date["2018-03-21"]["amount"]), Decimal("1.75"))
        self.assertEqual(Decimal(by_date["2018-06-19"]["amount"]), Decimal("1.75"))
        # These ordinary dividends are missing from the source adjustment factors.
        self.assertEqual(Decimal(by_date["2021-09-21"]["amount"]), Decimal("3.60"))
        self.assertEqual(Decimal(by_date["2024-06-24"]["amount"]), Decimal("5.25"))
        self.assertEqual(by_date["2024-06-24"]["split_divisor"], "10")
        self.assertEqual(by_date["2024-09-19"]["split_divisor"], "1")
        self.assertEqual(len(dividends), 39)
        self.assertNotIn("2024-12-23", by_date)


class PriceAuditTests(unittest.TestCase):
    def test_large_market_move_is_not_a_distribution(self):
        """Flag a change in adjustment factor, not an actual large market-price return."""
        rows = [
            {
                "ticker": "TEST",
                "date": "2020-01-02",
                "close": "10",
                "adjusted_close": "5",
            },
            {
                "ticker": "TEST",
                "date": "2020-01-03",
                "close": "20",
                "adjusted_close": "10",
            },
        ]

        companies, events = scan_adjustment_factors(rows)

        self.assertEqual(events, [])
        self.assertIsNone(companies["TEST"]["last_adjustment"])
        rows[1]["adjusted_close"] = "12"
        self.assertEqual(len(scan_adjustment_factors(rows)[1]), 1)

    def test_later_common_dividend_exposes_truncated_adjustments(self):
        """Report a candidate gap without inventing an ex-date or a correction amount."""
        body = {
            "facts": {
                "us-gaap": {
                    "CommonStockDividendsPerShareCashPaid": {
                        "units": {
                            "USD/shares": [
                                {
                                    "start": "2024-07-01",
                                    "end": "2024-09-30",
                                    "filed": "2024-11-01",
                                    "val": 1,
                                },
                            ]
                        }
                    }
                }
            }
        }
        statistics = {"last_adjustment": "2021-06-21"}

        result = find_dividend_coverage_gap(statistics, body, "2024-12-20")

        assert result is not None
        self.assertEqual(result["sec_evidence"]["val"], 1)
        self.assertNotIn("ex_date", result)
        self.assertIsNone(
            find_dividend_coverage_gap(
                {"last_adjustment": "2024-09-20"}, body, "2024-12-20"
            )
        )

    def test_subsidiary_or_later_filing_dividends_are_not_common_share_proof(self):
        """Do not infer public-share dividends from aggregate cash flows or future reports."""
        body = {
            "facts": {
                "us-gaap": {
                    "PaymentsOfDividends": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2024-09-30",
                                    "filed": "2024-11-01",
                                    "val": 8000000,
                                }
                            ]
                        }
                    },
                    "CommonStockDividendsPerShareDeclared": {
                        "units": {
                            "USD/shares": [
                                {"end": "2024-09-30", "filed": "2025-02-01", "val": 1}
                            ]
                        }
                    },
                }
            }
        }
        self.assertIsNone(
            find_dividend_coverage_gap({"last_adjustment": None}, body, "2024-12-20")
        )


if __name__ == "__main__":
    unittest.main()
