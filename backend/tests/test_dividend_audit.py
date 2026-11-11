"""Tests for the dividend audit on small invented price series with known errors."""

import unittest
from decimal import Decimal

from app.data_pipeline.dividend_audit import (
    audit_company,
    close_pair_dates,
    explain_fiscal_year,
    format_amount,
    reported_dividends,
    separate_reviewed_flags,
    source_rows_for,
    traded_price_factor,
    vendor_events,
)
from app.data_pipeline.price_adjustments import cash_adjustment

NO_RULES = {"actions": [], "excluded_vendor_events": []}
CLOSE = Decimal(100)


def quarterly_sessions(years):
    """One session every five days is enough to place events on known dates."""
    days = []
    for year in years:
        for month in range(1, 13):
            for day in (1, 6, 11, 16, 21, 26):
                days.append(f"{year}-{month:02d}-{day:02d}")
    return days


def price_rows(days, cash_by_date, close=CLOSE):
    """Build rows whose Adjusted Close contains exactly these cash amounts."""
    factor = Decimal(1)
    rows = []
    for day in reversed(days):
        rows.append(
            {"date": day, "close": str(close), "adjusted_close": str(close * factor)}
        )
        if day in cash_by_date:
            factor *= cash_adjustment(close, Decimal(cash_by_date[day]))
    rows.reverse()
    return rows


def regular_quarterly_cash(years, amount):
    return {
        f"{year}-{month:02d}-11": amount for year in years for month in (2, 5, 8, 11)
    }


def source_rows(cash_by_date):
    return [
        {"ex_date": day, "amount": str(amount)}
        for day, amount in sorted(cash_by_date.items())
    ]


def calendar_year_reported(years, value):
    return {(f"{year}-01-01", f"{year}-12-31"): [Decimal(value)] for year in years}


class DividendAuditTests(unittest.TestCase):
    YEARS = (2017, 2018, 2019)
    DAYS = quarterly_sessions(YEARS)

    def audit(self, cash, source, reported):
        rows = price_rows(self.DAYS, cash)
        return audit_company(
            "TEST", rows, source_rows(source), reported, NO_RULES, set()
        )

    def checks(self, result):
        return sorted({(flag["check"], flag["date"]) for flag in result["flags"]})

    def test_agreeing_sources_leave_no_flags(self):
        """Vendor, source and SEC agree, so nothing needs review."""
        cash = regular_quarterly_cash(self.YEARS, "0.50")

        result = self.audit(cash, cash, calendar_year_reported(self.YEARS, "2.00"))

        self.assertEqual(result["flags"], [])
        self.assertEqual(result["matched"], len(cash))
        self.assertEqual(result["years"], {"direct": 3})

    def test_foreign_company_dividends_are_flagged(self):
        """COR case: the vendor mixes in another company's dividends under its ticker."""
        own = regular_quarterly_cash(self.YEARS, "0.40")
        foreign = {"2018-03-26": "1.10", "2018-06-26": "1.22"}

        result = self.audit(
            {**own, **foreign}, own, calendar_year_reported(self.YEARS, "1.60")
        )

        flagged = self.checks(result)
        self.assertIn(("vendor_only", "2018-03-26"), flagged)
        self.assertIn(("vendor_only", "2018-06-26"), flagged)
        self.assertIn(("fiscal_year", "2018-12-31"), flagged)

    def test_amount_on_the_wrong_scale_is_flagged(self):
        """ETN case: 0.073 instead of 0.73 in one quarter."""
        right = regular_quarterly_cash(self.YEARS, "0.73")
        wrong = {**right, "2018-05-11": "0.073"}

        result = self.audit(wrong, right, calendar_year_reported(self.YEARS, "2.92"))

        self.assertIn(("amount", "2018-05-11"), self.checks(result))

    def test_rescaled_price_with_unscaled_dividends_is_flagged(self):
        """T case: the vendor divided prices by 1.324 but kept the old dividend amounts.

        As implied by the vendor, cash on the traded basis is 1.324 times too large in
        every quarter before the event, and SEC confirms the smaller amount.
        """
        rules = {
            "actions": [],
            "excluded_vendor_events": [
                {
                    "ticker": "TEST",
                    "date": "2020-01-01",
                    "vendor_new_shares": 1324,
                    "vendor_old_shares": 1000,
                }
            ],
        }
        cash = regular_quarterly_cash(self.YEARS, "0.50")
        rows = price_rows(self.DAYS, cash)

        result = audit_company(
            "TEST",
            rows,
            source_rows(cash),
            calendar_year_reported(self.YEARS, "2.00"),
            rules,
            set(),
        )

        self.assertEqual(len(result["flags"]), 12 + 3)
        self.assertEqual(result["matched"], 0)

    def test_duplicated_dividend_is_flagged_by_its_interval(self):
        """NSC case: the same amount twice a week apart, which DoltHub repeats too.

        A run of four equal payments still matches SEC, so only the interval shows it.
        """
        cash = regular_quarterly_cash(self.YEARS, "0.59")
        cash["2018-05-16"] = "0.59"

        result = self.audit(cash, cash, calendar_year_reported(self.YEARS, "2.36"))

        self.assertIn(("close_pair", "2018-05-16"), self.checks(result))

    def test_event_both_sources_share_but_sec_lacks_is_flagged(self):
        """HCA case: vendor and DoltHub agree on payments that the SEC total excludes."""
        cash = regular_quarterly_cash(self.YEARS, "0.43")
        for month in (5, 8, 11):
            del cash[f"2018-{month:02d}-11"]
        cash["2018-10-26"] = "0.072"
        cash["2018-12-21"] = "0.268"

        result = self.audit(
            cash,
            cash,
            {
                **calendar_year_reported((2017, 2019), "1.72"),
                ("2018-01-01", "2018-12-31"): [Decimal("0.43")],
            },
        )

        self.assertIn(("fiscal_year", "2018-12-31"), self.checks(result))

    def test_dividend_missing_from_the_vendor_is_flagged(self):
        """M10 case: DoltHub and SEC have a dividend without a vendor factor change."""
        right = regular_quarterly_cash(self.YEARS, "0.66")
        vendor = {day: amount for day, amount in right.items() if day != "2018-08-11"}

        result = self.audit(vendor, right, calendar_year_reported(self.YEARS, "2.64"))

        self.assertIn(("source_only", "2018-08-11"), self.checks(result))

    def test_gap_in_the_source_confirmed_by_sec_is_not_flagged(self):
        """The second source lacks a row, but the vendor total equals the SEC total."""
        cash = regular_quarterly_cash(self.YEARS, "0.50")
        source = {day: amount for day, amount in cash.items() if day != "2018-08-11"}

        result = self.audit(cash, source, calendar_year_reported(self.YEARS, "2.00"))

        self.assertEqual(result["flags"], [])

    def test_regular_event_without_any_sec_year_is_not_flagged(self):
        """A source gap in a year with no SEC tag repeats the neighbouring payments."""
        cash = regular_quarterly_cash(self.YEARS, "0.50")
        source = {day: amount for day, amount in cash.items() if day != "2018-08-11"}

        result = self.audit(cash, source, {})

        self.assertEqual(result["flags"], [])

    def test_ticker_of_another_company_is_not_used(self):
        """COR rows before the rename belong to CoreSite; ABC rows are Cencora's."""
        rows_by_symbol = {
            "COR": [
                {"ex_date": "2019-03-28", "amount": "1.10"},
                {"ex_date": "2024-02-08", "amount": "0.51"},
            ],
            "ABC": [{"ex_date": "2019-02-15", "amount": "0.40"}],
        }
        history = {
            "COR": [
                {"symbol": "ABC", "from": "2015-01-01", "through": "2023-08-29"},
                {"symbol": "COR", "from": "2023-08-30", "through": "2026-12-31"},
            ]
        }

        rows = source_rows_for("COR", rows_by_symbol, history)

        self.assertEqual([row["ex_date"] for row in rows], ["2019-02-15", "2024-02-08"])


class FiscalYearExplanationTests(unittest.TestCase):
    def events(self, amounts):
        days = [
            "2017-11-11",
            "2018-02-11",
            "2018-05-11",
            "2018-08-11",
            "2018-11-11",
            "2019-02-11",
        ]
        return [
            {"index": position, "date": day, "cash": Decimal(amount)}
            for position, (day, amount) in enumerate(zip(days, amounts, strict=True))
        ]

    def explain(self, amounts, value):
        period = ("2018-01-01", "2018-12-31")
        _, _, explanation = explain_fiscal_year(
            "TEST", self.events(amounts), period, [Decimal(value)], 4, NO_RULES
        )
        return explanation

    def test_declared_dividend_after_raise_is_timing(self):
        """LLY pattern: the raise is declared in December and paid in the next year."""
        amounts = ["1.00", "1.00", "1.00", "1.00", "1.00", "1.20"]

        self.assertEqual(self.explain(amounts, "4.20"), "timing")

    def test_quarter_tagged_as_year_is_single_payment(self):
        """V pattern: a filing tags one quarterly payment with the annual period."""
        amounts = ["0.25", "0.25", "0.25", "0.25", "0.25", "0.25"]

        self.assertEqual(self.explain(amounts, "0.25"), "single_payment")

    def test_unrelated_total_is_mismatch(self):
        """A reported total that no run of payments explains stays a mismatch."""
        amounts = ["0.50", "0.50", "0.50", "0.50", "0.50", "0.50"]

        self.assertEqual(self.explain(amounts, "3.10"), "mismatch")


class ShareBasisTests(unittest.TestCase):
    def test_vendor_event_converts_to_the_traded_price(self):
        """Vendor Close before a 1.324 event is scaled; after it, it is the traded price."""
        rules = {
            "actions": [],
            "excluded_vendor_events": [
                {
                    "ticker": "T",
                    "date": "2022-04-11",
                    "vendor_new_shares": 1324,
                    "vendor_old_shares": 1000,
                }
            ],
        }

        self.assertEqual(
            traded_price_factor("T", "2022-04-08", rules), Decimal("1.324")
        )
        self.assertEqual(traded_price_factor("T", "2022-04-11", rules), Decimal(1))

    def test_split_the_vendor_applied_late_is_not_undone(self):
        """APH 2026: the vendor divided only sessions from 31 Aug, so earlier prices are traded."""
        rules = {
            "actions": [
                {
                    "ticker": "APH",
                    "date": "2026-09-08",
                    "new_shares": 2,
                    "old_shares": 1,
                    "vendor_new_shares": 2,
                    "vendor_old_shares": 1,
                    "vendor_prices_unadjusted_before": "2026-08-31",
                }
            ],
            "excluded_vendor_events": [],
        }

        self.assertEqual(traded_price_factor("APH", "2025-12-30", rules), Decimal(1))
        self.assertEqual(traded_price_factor("APH", "2026-09-01", rules), Decimal(2))

    def test_implied_cash_is_on_the_traded_basis(self):
        """A 0.50 dividend on prices later divided by two is 0.50 as traded, not 0.25."""
        rules = {
            "actions": [
                {
                    "ticker": "TEST",
                    "date": "2020-01-01",
                    "new_shares": 2,
                    "old_shares": 1,
                    "vendor_new_shares": 2,
                    "vendor_old_shares": 1,
                }
            ],
            "excluded_vendor_events": [],
        }
        rows = price_rows(["2019-01-01", "2019-01-02"], {"2019-01-02": "0.25"})

        events = vendor_events("TEST", rows, rules)

        self.assertEqual(format_amount(events[0]["cash"]), "0.5000")

    def test_later_filing_is_put_on_the_period_end_basis(self):
        """A filing after a 4-for-1 split reports a quarter of the original annual amount."""
        rules = {
            "actions": [
                {
                    "ticker": "TEST",
                    "date": "2020-08-31",
                    "new_shares": 4,
                    "old_shares": 1,
                }
            ],
            "excluded_vendor_events": [],
        }
        body = {
            "facts": {
                "us-gaap": {
                    "CommonStockDividendsPerShareDeclared": {
                        "units": {
                            "USD/shares": [
                                {
                                    "start": "2018-01-01",
                                    "end": "2018-12-31",
                                    "val": "2.80",
                                    "form": "10-K",
                                    "filed": "2019-02-01",
                                },
                                {
                                    "start": "2018-01-01",
                                    "end": "2018-12-31",
                                    "val": "0.70",
                                    "form": "10-K",
                                    "filed": "2020-10-30",
                                },
                                {
                                    "start": "2018-10-01",
                                    "end": "2018-12-31",
                                    "val": "0.70",
                                    "form": "10-K",
                                    "filed": "2019-02-01",
                                },
                            ]
                        }
                    }
                }
            }
        }

        reported = reported_dividends("TEST", [body], rules)

        self.assertEqual(
            reported, {("2018-01-01", "2018-12-31"): [Decimal("2.80"), Decimal("2.80")]}
        )

    def test_filing_on_the_split_date_is_already_on_the_new_share_count(self):
        """K11: as for EPS, a report filed on the split date states the amount per new
        share, so the 4-for-1 split still restores it to the period-end basis."""
        rules = {
            "actions": [
                {
                    "ticker": "TEST",
                    "date": "2020-08-31",
                    "new_shares": 4,
                    "old_shares": 1,
                }
            ],
            "excluded_vendor_events": [],
        }
        body = {
            "facts": {
                "us-gaap": {
                    "CommonStockDividendsPerShareDeclared": {
                        "units": {
                            "USD/shares": [
                                {
                                    "start": "2018-01-01",
                                    "end": "2018-12-31",
                                    "val": "0.70",
                                    "form": "10-K",
                                    "filed": "2020-08-31",
                                },
                            ]
                        }
                    }
                }
            }
        }

        reported = reported_dividends("TEST", [body], rules)

        self.assertEqual(reported, {("2018-01-01", "2018-12-31"): [Decimal("2.80")]})


class ReviewedFlagTests(unittest.TestCase):
    def test_review_covers_only_the_values_it_saw(self):
        """A changed amount is unreviewed again; a review without a flag is reported."""
        reviewed = [
            {
                "ticker": "AAA",
                "check": "amount",
                "date": "2020-01-01",
                "vendor": "0.5000",
                "source": "0.2500",
            },
            {
                "ticker": "BBB",
                "check": "vendor_only",
                "date": "2021-01-01",
                "vendor": "1.0000",
                "source": None,
            },
        ]
        flags = [dict(reviewed[0])]

        unreviewed, matched, not_found = separate_reviewed_flags(flags, reviewed)
        self.assertEqual((unreviewed, matched), ([], 1))
        self.assertEqual(
            not_found,
            [{"ticker": "BBB", "check": "vendor_only", "date": "2021-01-01"}],
        )

        changed = [dict(reviewed[0], vendor="0.6000")]
        unreviewed, matched, _ = separate_reviewed_flags(changed, reviewed)
        self.assertEqual((len(unreviewed), matched), (1, 0))


class ClosePairTests(unittest.TestCase):
    def test_monthly_payer_has_no_close_pairs(self):
        """The interval is judged against the company's own usual interval."""
        events = [
            {
                "index": index * 21,
                "date": f"2020-{index + 1:02d}-15",
                "cash": Decimal(1),
            }
            for index in range(8)
        ]

        self.assertEqual(close_pair_dates(events), set())


if __name__ == "__main__":
    unittest.main()
