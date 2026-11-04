import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_pipeline.document_check import (
    Filing,
    check_automatic_version,
    check_version,
    confirmed_elsewhere,
    number,
    parse_tables,
    period_fits,
    separate_reviewed,
    shifted_task,
    wrong_row_task,
    wrong_row_values,
    wrong_rows_of_filing,
)


def revenue_task(value="9467000000", period_end="2025-12-31", **changes):
    """A served version as the check receives it from the database."""
    task = {
        "ticker": "ZTS",
        "metric": "revenue",
        "accession": "0000000000-26-000001",
        "form": "10-K",
        "known_from": "2026-02-15",
        "period_start": period_end[:4] + "-01-01",
        "period_end": period_end,
        "value": value,
        "calculation": None,
        "eps_basis": "diluted",
        "reported_eps": None,
        "sources": [],
    }
    task.update(changes)
    return task


def filing_of(*tables):
    return Filing([{"type": "10-K", "tables": list(tables)}])


STATEMENT = [
    ["Year Ended December 31", "2025", "2024"],
    ["Total revenue", "9,467", "9,256"],
    ["Total costs and expenses", "6,000", "5,900"],
    ["Net income", "2,486", "2,344"],
]
BALANCE_SHEET = [
    ["December 31", "2025", "2024"],
    ["Total liabilities", "12,136", "9,467"],
    ["Total assets", "15,000", "14,000"],
]


class ParsingTests(unittest.TestCase):
    def test_tables_drop_layout_cells_and_join_closing_brackets(self):
        """A split bracket stays part of its negative number and "$" cells disappear."""
        html = (
            "<table><tr><td>Net loss</td><td>$</td><td>(12</td><td>)</td></tr></table>"
        )

        tables = parse_tables(html)

        self.assertEqual(tables, [[["Net loss", "(12)"]]])

    def test_numbers_read_brackets_as_negative(self):
        """Statements print a loss in brackets, and a footnote mark may follow it."""
        self.assertEqual(number("(1,234.5)"), Decimal("-1234.5"))
        self.assertEqual(number("$ 12"), Decimal(12))
        self.assertIsNone(number("Total"))


class VersionCheckTests(unittest.TestCase):
    def test_value_in_its_row_and_column_passes(self):
        """The ordinary case: the revenue row, the column of the period."""
        verdict, evidence = check_version(revenue_task(), filing_of(STATEMENT))

        self.assertEqual(verdict, "pass_direct")
        self.assertIn("Total revenue", evidence)

    def test_total_liabilities_of_the_same_amount_is_rejected(self):
        """Regression for M14: the old label "total" accepted this ZTS row of liabilities."""
        task = revenue_task(value="9467000000", period_end="2024-12-31")

        verdict, _ = check_version(task, filing_of(BALANCE_SHEET))

        self.assertFalse(verdict.startswith("pass"))

    def test_revenue_net_of_interest_expense_keeps_its_label(self):
        """Banks name revenue with the word expense; excluding it would queue them all."""
        table = [
            ["Year Ended December 31", "2025", "2024"],
            ["Total revenue, net of interest expense", "9,467", "9,256"],
        ]

        verdict, _ = check_version(revenue_task(), filing_of(table))

        self.assertEqual(verdict, "pass_direct")

    def test_a_wrong_year_is_rejected(self):
        """The value stands in the table, but under another period."""
        task = revenue_task(period_end="2024-12-31")

        verdict, _ = check_version(task, filing_of(STATEMENT))

        self.assertFalse(verdict.startswith("pass"))

    def test_total_row_without_label_takes_the_section_heading(self):
        """Public Storage prints its revenue total as a row of numbers under "Revenues:"."""
        table = [
            ["Year Ended December 31", "2025", "2024"],
            ["Revenues:"],
            ["Self-storage facilities", "9,000", "8,800"],
            ["Ancillary operations", "467", "456"],
            ["9,467", "9,256"],
        ]

        verdict, _ = check_version(revenue_task(), filing_of(table))

        self.assertEqual(verdict, "pass_direct")

    def test_footnote_cells_are_not_columns(self):
        """Danaher marks restated years with "*": the mark must not shift the columns."""
        table = [
            ["Year Ended December 31", "2025", "2024", "2023"],
            ["Net sales", "9,000", "8,000", "*", "9,467", "(a)(b)"],
        ]
        task = revenue_task(period_end="2023-12-31")

        verdict, _ = check_version(task, filing_of(table))

        self.assertEqual(verdict, "pass_direct")

    def test_row_in_stacked_equity_statement_belongs_to_the_next_year(self):
        """Stacked statements date rows by the balance above, not by a header."""
        table = [
            ["Common stock", "Retained earnings", "Total"],
            ["Balance, December 31, 2024", "100", "200", "300"],
            ["Net income", "-", "2,486", "2,486"],
            ["Balance, December 31, 2025", "100", "2,686", "2,786"],
            ["Net income", "-", "3,000", "3,000"],
        ]
        income_2025 = revenue_task(
            value="2486000000", metric="net_income", period_end="2025-12-31"
        )
        wrongly_dated = revenue_task(
            value="3000000000", metric="net_income", period_end="2025-12-31"
        )

        filing = filing_of(table)

        self.assertEqual(check_version(income_2025, filing)[0], "pass_direct")
        self.assertFalse(check_version(wrongly_dated, filing)[0].startswith("pass"))

    def test_heading_does_not_rename_a_row_with_its_own_label(self):
        """Regression: under "Statement of Earnings" a revenue row is not net income."""
        table = [
            ["Year Ended December 31", "2025", "2024"],
            ["Statement of Earnings Data:"],
            ["Net revenues", "9,467", "9,256"],
        ]
        task = revenue_task(metric="net_income")

        verdict, _ = check_version(task, filing_of(table))

        self.assertFalse(verdict.startswith("pass"))

    def test_balance_row_outside_a_statement_of_equity_is_rejected(self):
        """Regression: "Balance" in a goodwill table passed as equity."""
        goodwill = [
            ["Goodwill roll-forward"],
            ["Balance as of December 31, 2025", "9,467"],
        ]
        equity_statement = [
            ["Common stock", "Retained earnings", "Total"],
            ["Balance as of December 31, 2025", "100", "9,367", "9,467"],
        ]
        task = revenue_task(metric="equity")

        self.assertFalse(check_version(task, filing_of(goodwill))[0].startswith("pass"))
        self.assertEqual(
            check_version(task, filing_of(equity_statement))[0], "pass_direct"
        )

    def test_quarter_columns_under_a_year_are_rejected(self):
        """Regression: a quarter of the year under the year's header is not the year."""
        under_heading = [
            ["Unaudited", "2025", "2024"],
            ["First Quarter"],
            ["Net sales", "9,467", "9,256"],
        ]
        above_header = [
            ["Three Months Ended"],
            ["Period", "2025", "2024"],
            ["Net sales", "9,467", "9,256"],
        ]

        task = revenue_task()

        self.assertFalse(
            check_version(task, filing_of(under_heading))[0].startswith("pass")
        )
        self.assertFalse(
            check_version(task, filing_of(above_header))[0].startswith("pass")
        )

    def test_other_activities_are_not_operating_cash_flow(self):
        """Regression: "investing activities - continuing operations" contains "operat"."""
        table = [
            ["Year Ended December 31", "2025", "2024"],
            [
                "Net cash used in investing activities - continuing operations",
                "9,467",
                "1",
            ],
        ]
        task = revenue_task(metric="operating_cash_flow")

        verdict, _ = check_version(task, filing_of(table))

        self.assertFalse(verdict.startswith("pass"))

    def test_calculated_value_needs_all_its_components(self):
        """A calculated value passes through its rows only when every row is found."""
        table = [
            ["Year Ended December 31", "2025", "2024"],
            ["Noninterest revenue", "100", "90"],
            ["Net interest income", "50", "40"],
        ]
        sources = [
            {"tag": "NoninterestIncome", "value": "100"},
            {"tag": "InterestIncomeExpenseNet", "value": "50"},
        ]
        task = revenue_task(value="150", calculation="sum", sources=sources)
        missing_part = revenue_task(
            value="151",
            calculation="sum",
            sources=[sources[0], {"tag": "InterestIncomeExpenseNet", "value": "51"}],
        )

        filing = filing_of(table)

        self.assertEqual(check_version(task, filing)[0], "pass_components")
        self.assertFalse(check_version(missing_part, filing)[0].startswith("pass"))

    def test_missing_extract_and_empty_filing_go_to_the_queue(self):
        """A filing that could not be read is reported, not silently passed."""
        self.assertEqual(check_version(revenue_task(), None)[0], "queue_no_document")
        self.assertEqual(
            check_version(revenue_task(), Filing([]))[0], "queue_no_primary_document"
        )


class AutomaticVersionTests(unittest.TestCase):
    """Values taken by rule, against the tables of their own report."""

    INCOME_STATEMENT = [
        ["Year Ended December 31", "2025", "2024"],
        ["Income from operations", "2,000", "1,800"],
        ["Purchases of property and equipment", "(500)", "(400)"],
    ]

    def verdict(self, metric, value, revenue=None):
        task = revenue_task(value, metric=metric, revenue=revenue)
        return check_automatic_version(task, filing_of(self.INCOME_STATEMENT))

    def test_value_in_its_row_and_year_passes(self):
        """Cash flow rows show outflows in brackets, so both signs are tried."""
        self.assertEqual(self.verdict("operating_income", "2000000000"), "pass_direct")
        self.assertEqual(self.verdict("capex", "500000000"), "pass_direct")

    def test_value_of_another_year_is_not_a_match(self):
        """1,800 stands in the table, but for 2024, and differs from 2,000 by 10%."""
        self.assertEqual(
            self.verdict("operating_income", "1800000000"), "queue_other_number"
        )

    def test_small_difference_passes_within_the_tolerance(self):
        """A difference of 2% is under the threshold of the correction rule."""
        self.assertEqual(
            self.verdict("operating_income", "2040000000"), "pass_within_tolerance"
        )

    def test_report_without_a_fitting_row_is_listed(self):
        """Nothing to compare with is reported, not counted as agreement."""
        self.assertEqual(self.verdict("assets", "1000000000"), "queue_no_row")

    def test_tolerance_needs_a_row_of_the_same_quantity(self):
        """Regression M31: a near number in an unrelated row confirmed nothing.

        A wide label fits "Net realized capital gains" and "Expected return on plan
        assets", which AIG's capex and Berkshire's assets were once passed against.
        """
        unrelated_rows = (
            ("capex", ["Net realized capital gains", "304", "290"], "310000000"),
            ("assets", ["Expected return on plan assets", "975", "900"], "990000000"),
        )
        for metric, row, value in unrelated_rows:
            with self.subTest(metric=metric):
                table = [["Year Ended December 31", "2025", "2024"], row]
                task = revenue_task(value, metric=metric, revenue=None)

                verdict = check_automatic_version(task, filing_of(table))

                self.assertEqual(verdict, "queue_no_row")

    def test_tolerance_rejects_rows_of_another_quantity_with_the_same_words(self):
        """Basic shares normally lie within 5% of diluted, so they would hide a wrong count."""
        rows = (
            ("shares_diluted", "Weighted average shares outstanding - basic"),
            ("shares_diluted", "Average price per share"),
            ("operating_income", "Other non-operating income"),
            ("operating_income", "Total other operating income"),
            ("preferred_value", "Proceeds from issuance of preferred stock"),
            ("preferred_value", "Preferred stock dividends"),
            ("assets", "Assets held for sale"),
            ("assets", "Total assets under management"),
            ("capex", "Capital expenditures accrued in accounts payable"),
        )
        for metric, label in rows:
            with self.subTest(label=label):
                table = [
                    ["Year Ended December 31", "2025", "2024"],
                    [label, "1,000", "900"],
                ]
                task = revenue_task("1030000000", metric=metric, revenue=None)

                verdict = check_automatic_version(task, filing_of(table))

                self.assertEqual(verdict, "queue_no_row")

    def test_tolerance_rejects_measures_outside_gaap(self):
        """Regression M31: an adjusted figure near the value does not confirm it."""
        table = [
            ["Year Ended December 31", "2025", "2024"],
            ["Adjusted operating income", "2,000", "1,800"],
        ]
        task = revenue_task("2040000000", metric="operating_income", revenue=None)

        verdict = check_automatic_version(task, filing_of(table))

        self.assertEqual(verdict, "queue_no_row")

    def test_adjusted_share_denominator_counts_as_its_row(self):
        """ "Adjusted weighted average" is the GAAP diluted denominator (Citigroup, D.R. Horton)."""
        table = [
            ["Year Ended December 31", "2025", "2024"],
            ["Adjusted weighted average common shares", "1,000", "990"],
        ]
        task = revenue_task("1000000000", metric="shares_diluted", revenue=None)

        verdict = check_automatic_version(task, filing_of(table))

        self.assertEqual(verdict, "pass_within_tolerance")

    def test_missing_report_is_listed(self):
        """A filing that was never downloaded cannot confirm anything."""
        task = revenue_task(metric="assets", revenue=None)

        self.assertEqual(check_automatic_version(task, None), "queue_no_document")


class ConfirmationTests(unittest.TestCase):
    def test_same_value_in_another_filing_confirms_only_the_same_period(self):
        """A 10-K/A without statements borrows only an equal value of the same period."""
        queued = revenue_task(accession="amended")
        other_period = revenue_task(accession="amended", period_end="2024-12-31")
        other_value = revenue_task(accession="amended", value="9467000001")
        passed = revenue_task(accession="original")

        result = confirmed_elsewhere([queued, other_period, other_value], [passed])

        self.assertEqual(result, [queued])

    def test_review_covers_only_the_value_it_saw(self):
        """A changed value leaves the queue item unreviewed, and the old review is stale."""
        queue = [revenue_task(value="100")]
        same_value_review = {**queue[0], "value": "100.00"}
        old_value_review = {**queue[0], "value": "99"}

        unreviewed, stale = separate_reviewed(queue, [same_value_review])
        self.assertEqual((unreviewed, stale), ([], []))

        unreviewed, stale = separate_reviewed(queue, [old_value_review])
        self.assertEqual(unreviewed, queue)
        self.assertEqual(stale, [old_value_review])


class NegativeControlTests(unittest.TestCase):
    def test_planted_errors_fail_on_a_sample_statement(self):
        """What the control plants must fail here, or its counts would mean nothing."""
        filing = filing_of(STATEMENT, BALANCE_SHEET)
        task = revenue_task()

        wrong_year = shifted_task(task, "wrong_year")
        wrong_value = shifted_task(task, "wrong_value")
        positions = wrong_rows_of_filing(filing, "revenue")
        wrong_values = wrong_row_values(task, filing, positions)

        self.assertEqual(wrong_year["period_end"], "2024-12-28")
        self.assertEqual(Decimal(wrong_value["value"]), Decimal("9561670000"))
        self.assertEqual(
            wrong_values, {Decimal(6000), Decimal(2486), Decimal(12136), Decimal(15000)}
        )
        self.assertFalse(check_version(wrong_year, filing)[0].startswith("pass"))
        self.assertFalse(check_version(wrong_value, filing)[0].startswith("pass"))
        for value in wrong_values:
            planted = wrong_row_task(task, value)
            self.assertFalse(check_version(planted, filing)[0].startswith("pass"))

    def test_period_of_a_header_of_years(self):
        """Fiscal years ending in January are named by the year they started in."""
        table = [["Fiscal year", "2024", "2023"], ["Net sales", "9,467", "9,256"]]
        january_year = revenue_task(period_end="2025-01-05", known_from="2025-03-01")

        position = period_fits(january_year, table, 1, 1, table[1])

        self.assertEqual(position, "column")


if __name__ == "__main__":
    unittest.main()
