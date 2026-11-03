"""Regression tests for reviewed price corrections, independent of network access."""

import unittest
from decimal import Decimal

from app.data_pipeline.price_adjustments import (
    apply_adjustments,
    find_dividend_coverage_gap,
    scan_adjustment_factors,
    separate_reviewed_events,
    separate_reviewed_gaps,
)
from app.data_pipeline.source_files import MANIFEST, ROOT, read_json


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

    def test_missing_dividend_scales_only_earlier_vendor_adjusted_close(self):
        """A dividend the vendor omits lowers earlier Adjusted Close by (Close - cash) / Close.

        The vendor factor 0.99 stays. With the dividend, the ex-date return is
        98 / (100 - 1) - 1 (about -1.01%) instead of a -2% loss; later sessions are
        unchanged.
        """
        rows = [
            {"date": "2025-09-11", "close": "100", "adjusted_close": "99"},
            {"date": "2025-09-12", "close": "98", "adjusted_close": "97.02"},
            {"date": "2025-09-15", "close": "99", "adjusted_close": "98.01"},
        ]
        rule = {
            "method": "missing_cash_dividends",
            "dividends": [
                {"ex_date": "2025-09-12", "amount": "1", "split_divisor": "1"},
            ],
        }

        corrected = apply_adjustments(rows, rule)
        total_return = corrected["2025-09-12"] / corrected["2025-09-11"] - 1

        self.assertEqual(corrected["2025-09-11"], Decimal("98.01"))
        self.assertEqual(corrected["2025-09-12"], Decimal("97.02"))
        self.assertEqual(corrected["2025-09-15"], Decimal("98.01"))
        self.assertAlmostEqual(float(total_return), 98 / 99 - 1, places=12)

    def test_missing_dividend_refuses_a_day_the_vendor_already_adjusts(self):
        """If the vendor factor changes on the ex-date, adding the dividend would double it."""
        rows = [
            {"date": "2025-09-11", "close": "100", "adjusted_close": "99"},
            {"date": "2025-09-12", "close": "98", "adjusted_close": "98"},
        ]
        rule = {
            "method": "missing_cash_dividends",
            "dividends": [
                {"ex_date": "2025-09-12", "amount": "1", "split_divisor": "1"},
            ],
        }

        with self.assertRaisesRegex(ValueError, "already adjusts"):
            apply_adjustments(rows, rule)

    def test_missing_dividend_refuses_a_vendor_dividend_a_few_sessions_away(self):
        """A vendor dividend two sessions after the reviewed ex-date would be counted twice.

        Dividend sources differ on ex-dates by days, so a flat factor on the ex-date
        alone does not show that the vendor lacks the dividend.
        """
        rows = [
            {"date": "2025-09-11", "close": "100", "adjusted_close": "99"},
            {"date": "2025-09-12", "close": "98", "adjusted_close": "97.02"},
            {"date": "2025-09-15", "close": "99", "adjusted_close": "98.01"},
            # The vendor factor moves from 0.99 to 1: its own 1% dividend.
            {"date": "2025-09-16", "close": "99", "adjusted_close": "99"},
        ]
        rule = {
            "method": "missing_cash_dividends",
            "dividends": [
                {"ex_date": "2025-09-12", "amount": "1", "split_divisor": "1"},
            ],
        }

        with self.assertRaisesRegex(ValueError, "near the ex-dividend date"):
            apply_adjustments(rows, rule)

    def test_missing_dividend_accepts_rounding_noise_on_the_ex_date(self):
        """A factor change of 5e-6 is the vendor's rounding, not an adjustment.

        Such noise occurs on days without any event in the vendor files; the
        earlier tolerance of 1e-6 refused a valid dividend on those days.
        """
        rows = [
            {"date": "2025-09-11", "close": "100", "adjusted_close": "99"},
            {"date": "2025-09-12", "close": "98", "adjusted_close": "97.0204851"},
        ]
        rule = {
            "method": "missing_cash_dividends",
            "dividends": [
                {"ex_date": "2025-09-12", "amount": "1", "split_divisor": "1"},
            ],
        }

        corrected = apply_adjustments(rows, rule)

        self.assertEqual(corrected["2025-09-11"], Decimal("98.01"))
        self.assertEqual(corrected["2025-09-12"], Decimal("97.0204851"))

    def test_reviewed_avgo_actions_correct_duplicate_and_missing_cash(self):
        """Keep issuer-announced common dividends, including actions absent from Kaggle."""
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
        # The window ends 2025-12-31: SEC cash paid 2.36 in fiscal 2025 (4 x 0.59),
        # then 0.65; the March 2026 ex-date lies after the window.
        self.assertEqual(Decimal(by_date["2024-12-23"]["amount"]), Decimal("0.59"))
        self.assertEqual(Decimal(by_date["2025-12-22"]["amount"]), Decimal("0.65"))
        self.assertEqual(len(dividends), 44)
        self.assertNotIn("2026-03-23", by_date)

    def test_reviewed_missing_dividends_are_those_above_the_threshold(self):
        """Only ETN keeps added dividends: its Adjusted Close moves over 1%.

        The other eight dividends found missing in M10 change their companies by less,
        so the source value stays (project-principles.md, section 5.1). This pins the
        list itself. That the vendor files really lack ETN's two dividends is checked
        by MissingDividendSourceTests.
        """
        active = read_json(MANIFEST)
        policy = read_json(ROOT / active["reviews"]["AVGO"]["rules_file"])
        missing = {
            (ticker, dividend["ex_date"], dividend["amount"])
            for ticker, rule in policy["companies"].items()
            if rule["method"] == "missing_cash_dividends"
            for dividend in rule["dividends"]
        }

        self.assertEqual(
            missing,
            {
                ("ETN", "2018-08-02", "0.66"),
                ("ETN", "2018-11-01", "0.66"),
            },
        )


class PriceAuditTests(unittest.TestCase):
    def test_only_the_exact_reviewed_factor_change_passes(self):
        """A reviewed special dividend is accepted; a new or changed event is not.

        COST 5.00 special dividend (5 Feb 2015) was reviewed with a 3.313% factor
        change. The same date with another change means the prices changed, and
        a reviewed event the screen no longer finds is reported as well.
        """
        reviewed = {
            ("COST", "2015-02-05"): {"factor_change_percent": "3.313"},
            ("TDG", "2016-10-20"): {"factor_change_percent": "9.315"},
        }
        events = [
            {"ticker": "COST", "date": "2015-02-05", "factor_change_percent": "3.313"},
            {"ticker": "PGR", "date": "2019-02-01", "factor_change_percent": "3.881"},
        ]

        unreviewed, matched, missing = separate_reviewed_events(events, reviewed)

        self.assertEqual(matched, 1)
        self.assertEqual([event["ticker"] for event in unreviewed], ["PGR"])
        self.assertEqual(missing, [{"ticker": "TDG", "date": "2016-10-20"}])

        changed = [dict(events[0], factor_change_percent="3.4")]
        unreviewed, matched, _ = separate_reviewed_events(changed, reviewed)
        self.assertEqual((len(unreviewed), matched), (1, 0))

    def test_only_the_exact_reviewed_coverage_gap_passes(self):
        """A reviewed gap is accepted; a new, changed or vanished gap is not."""
        gaps = {
            "CB": {"last_adjustment": "2025-06-13"},
            "JCI": {"last_adjustment": "2025-03-21"},
            "PGR": {"last_adjustment": "2025-01-02"},
        }
        reviewed = {
            "CB": {"last_adjustment": "2025-06-13"},
            "JCI": {"last_adjustment": "2025-06-23"},
            "TT": {"last_adjustment": "2025-06-06"},
        }

        unreviewed, missing = separate_reviewed_gaps(gaps, reviewed)

        self.assertEqual(sorted(unreviewed), ["JCI", "PGR"])
        self.assertEqual(missing, ["JCI", "TT"])

    def vendor_rows_with_dividends(self, cash_by_date):
        """Close stays at 100; Adjusted Close contains exactly these vendor amounts."""
        days = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]
        factor = Decimal(1)
        rows = []
        for day in reversed(days):
            rows.append(
                {"date": day, "close": "100", "adjusted_close": str(100 * factor)}
            )
            if day in cash_by_date:
                factor *= (100 - Decimal(cash_by_date[day])) / 100
        rows.reverse()
        return rows

    def implied_cash(self, corrected, earlier, later):
        return 100 * (1 - corrected[earlier] / corrected[later])

    def test_foreign_dividend_is_removed_and_own_one_stays(self):
        """COR case: removing CoreSite's 1.22 leaves Cencora's 0.40 exactly as it was."""
        rows = self.vendor_rows_with_dividends(
            {"2020-01-03": "0.40", "2020-01-07": "1.22"}
        )
        rule = {
            "method": "reviewed_dividend_changes",
            "changes": [
                {
                    "ex_date": "2020-01-07",
                    "vendor_amount": "1.22",
                    "amount": "0",
                    "split_divisor": "1",
                }
            ],
        }

        corrected = apply_adjustments(rows, rule)

        self.assertEqual(corrected["2020-01-06"], corrected["2020-01-07"])
        self.assertAlmostEqual(
            float(self.implied_cash(corrected, "2020-01-02", "2020-01-03")), 0.40, 9
        )
        self.assertEqual(corrected["2020-01-08"], Decimal(100))

    def test_dividend_on_the_old_scale_is_rescaled(self):
        """T case: 0.52 stands on prices divided by 1.324, so its share is 0.52 / 1.324."""
        rows = self.vendor_rows_with_dividends({"2020-01-07": "0.52"})
        rule = {
            "method": "reviewed_dividend_changes",
            "changes": [
                {
                    "ex_date": "2020-01-07",
                    "vendor_amount": "0.52",
                    "amount": "0.52",
                    "split_divisor": "1.324",
                }
            ],
        }

        corrected = apply_adjustments(rows, rule)

        self.assertAlmostEqual(
            float(self.implied_cash(corrected, "2020-01-06", "2020-01-07")),
            0.52 / 1.324,
            9,
        )

    def test_wrong_decimal_place_is_replaced(self):
        """ETN case: the vendor applied 0.073 where SEC reports 0.73."""
        rows = self.vendor_rows_with_dividends({"2020-01-07": "0.073"})
        rule = {
            "method": "reviewed_dividend_changes",
            "changes": [
                {
                    "ex_date": "2020-01-07",
                    "vendor_amount": "0.073",
                    "amount": "0.73",
                    "split_divisor": "1",
                }
            ],
        }

        corrected = apply_adjustments(rows, rule)

        self.assertAlmostEqual(
            float(self.implied_cash(corrected, "2020-01-06", "2020-01-07")), 0.73, 9
        )

    def test_distribution_the_vendor_omits_is_added(self):
        """OXY case: warrants worth 0.62 a share appear where the vendor shows nothing."""
        rows = self.vendor_rows_with_dividends({"2020-01-03": "0.40"})
        rule = {
            "method": "reviewed_dividend_changes",
            "changes": [
                {
                    "ex_date": "2020-01-07",
                    "vendor_amount": "0",
                    "amount": "0.61875",
                    "split_divisor": "1",
                }
            ],
        }

        corrected = apply_adjustments(rows, rule)

        self.assertAlmostEqual(
            float(self.implied_cash(corrected, "2020-01-06", "2020-01-07")), 0.61875, 9
        )
        self.assertAlmostEqual(
            float(self.implied_cash(corrected, "2020-01-02", "2020-01-03")), 0.40, 9
        )

    def test_change_requires_the_amount_the_vendor_shows(self):
        """A review written for another amount or a quiet session must not apply."""
        rows = self.vendor_rows_with_dividends({"2020-01-07": "0.073"})
        for ex_date, vendor_amount in (
            ("2020-01-07", "0.50"),
            ("2020-01-06", "0.073"),
        ):
            rule = {
                "method": "reviewed_dividend_changes",
                "changes": [
                    {
                        "ex_date": ex_date,
                        "vendor_amount": vendor_amount,
                        "amount": "0",
                        "split_divisor": "1",
                    }
                ],
            }
            with self.assertRaisesRegex(ValueError, "does not show"):
                apply_adjustments(rows, rule)

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


def vendor_factor(row):
    return Decimal(row["adjusted_close"]) / Decimal(row["close"])


@unittest.skipUnless(
    (ROOT / "data/raw/benjaminpo-v81").is_dir(),
    "Needs the pinned Benjaminpo v81 files in data/raw (outside Git)",
)
class MissingDividendSourceTests(unittest.TestCase):
    def test_added_dividends_fill_gaps_in_the_pinned_vendor_files(self):
        """Every added dividend is absent from the vendor files and applied exactly.

        Checked on the checksum-pinned vendor histories rather than on the rules: the
        vendor's factor stays within rounding noise for five sessions on each side of
        the ex-date, and the corrected series falls into the ex-date by exactly
        (prior Close - cash) / prior Close more than the vendor's own series.
        """
        from app.data_pipeline import benjaminpo

        manifest = read_json(MANIFEST)
        policy = benjaminpo.cash_adjustment_policy(manifest)
        tickers = benjaminpo.missing_dividend_tickers(
            policy, benjaminpo.selected_tickers(manifest)
        )
        checked = 0

        for ticker in tickers:
            source_rows = benjaminpo.read_history(ticker, manifest)
            rows = [dict(row) for row in source_rows]
            benjaminpo.correct_missing_dividends(rows, policy, ticker)
            positions = {row["date"]: index for index, row in enumerate(rows)}

            for dividend in policy["companies"][ticker]["dividends"]:
                position = positions[dividend["ex_date"]]
                with self.subTest(ticker=ticker, ex_date=dividend["ex_date"]):
                    for index in range(position - 5, position + 6):
                        vendor_change = (
                            vendor_factor(source_rows[index])
                            / vendor_factor(source_rows[index - 1])
                            - 1
                        )
                        self.assertLess(abs(vendor_change), Decimal("0.00001"))

                    prior_close = Decimal(source_rows[position - 1]["close"])
                    expected = (prior_close - Decimal(dividend["amount"])) / prior_close
                    scale_before = Decimal(rows[position - 1]["adjusted_close"]) / (
                        Decimal(source_rows[position - 1]["adjusted_close"])
                    )
                    scale_on_ex_date = Decimal(rows[position]["adjusted_close"]) / (
                        Decimal(source_rows[position]["adjusted_close"])
                    )
                    self.assertAlmostEqual(
                        scale_before / scale_on_ex_date,
                        expected,
                        delta=Decimal("1e-15"),
                    )
                checked += 1
            # Sessions after the last added dividend keep the vendor's values.
            self.assertEqual(
                rows[-1]["adjusted_close"], source_rows[-1]["adjusted_close"]
            )

        self.assertEqual(checked, 2)


if __name__ == "__main__":
    unittest.main()
