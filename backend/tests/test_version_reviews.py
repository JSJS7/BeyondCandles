"""Check that an earlier filing's version is approved only from its own statement table."""

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from test_net_income import statement
from test_stocks_api import reported_fact

from app.data_pipeline import operating_cash_flow as cash_flow
from app.data_pipeline import version_reviews

END = date(2020, 12, 31)


def task_with(fact, supporting_facts=()):
    """A version of one net-income cell whose filing reported the given fact."""
    return SimpleNamespace(
        cik=fact["source_cik"],
        end=END,
        cell_facts=[fact],
        supporting_facts=list(supporting_facts),
    )


def review_against(html, fact, supporting_facts=()):
    statements = [({"url": "https://www.sec.gov/R4.htm"}, html)]
    summary = {"url": "https://www.sec.gov/FilingSummary.xml"}
    with patch.object(
        version_reviews, "income_statements", return_value=(summary, statements)
    ):
        return version_reviews.review_net_income(
            task_with(fact, supporting_facts), {}, END
        )


class NetIncomeVersionTests(unittest.TestCase):
    def setUp(self):
        # AMD's first FY2017 report: 43 mln, later restated to -33 mln.
        self.fact = reported_fact(
            value=Decimal("43000000"),
            period_start=date(2020, 1, 1),
            period_end=END,
        )

    def test_parent_row_matching_the_fact_is_approved(self):
        """The rendered parent row equals the filing's fact, so the version is approved."""
        html = statement([("NetIncomeLoss", "Net income", "$ 43")])

        review = review_against(html, self.fact)

        self.assertEqual(review["value"], "43000000")
        self.assertEqual(review["components"][0]["fact"]["value"], "43000000")
        self.assertEqual(review["statement"]["url"], "https://www.sec.gov/R4.htm")

    def test_statement_showing_another_amount_is_not_approved(self):
        """A fact the table does not show (e.g. a later restatement) stays unreviewed."""
        html = statement([("NetIncomeLoss", "Net income", "$ (33)")])

        with self.assertRaises(ValueError):
            review_against(html, self.fact)


class UnstoredAllocationTests(unittest.TestCase):
    """Amphenol's first FY2015 report tags only group income and its allocation.

    The import stores the noncontrolling fact only where the latest report lacks a
    parent row, so the review reads it from the same filing and pins it.
    """

    def setUp(self):
        self.group = reported_fact(
            tag="ProfitLoss",
            value=Decimal("772300000"),
            period_start=date(2020, 1, 1),
            period_end=END,
        )
        self.allocation = reported_fact(
            tag="NetIncomeLossAttributableToNoncontrollingInterest",
            value=Decimal("8800000"),
            period_start=date(2020, 1, 1),
            period_end=END,
        )
        self.html = statement(
            [
                ("ProfitLoss", "Net income", "772.3"),
                (
                    "NetIncomeLossAttributableToNoncontrollingInterest",
                    "Less: Net income attributable to noncontrolling interests",
                    "(8.8)",
                ),
                (
                    "NetIncomeLossAvailableToCommonStockholdersBasic",
                    "Net income attributable to Amphenol Corporation",
                    "$ 763.5",
                ),
            ]
        )

    def test_unstored_allocation_reconciles_and_is_pinned(self):
        """Group income minus the filing's own allocation; only that one is pinned."""
        review = review_against(self.html, self.group, [self.allocation])

        self.assertEqual(review["value"], "763500000")
        group, allocation = review["components"]
        self.assertEqual(group["fact"]["tag"], "ProfitLoss")
        self.assertNotIn("statement_position", group)
        self.assertEqual(allocation["coefficient"], -1)
        self.assertTrue(allocation["statement_position"])

    def test_without_the_allocation_the_version_stays_unresolved(self):
        """Group income is never shown as parent income for lack of an allocation."""
        with self.assertRaisesRegex(ValueError, "Missing or conflicting fact"):
            review_against(self.html, self.group)


class SupportingFactTests(unittest.TestCase):
    def test_only_unstored_annual_facts_of_the_same_filing_are_attached(self):
        """Another filing, a quarter or an already stored fact is not added."""
        stored = reported_fact(
            tag="NetIncomeLossAvailableToCommonStockholdersBasic",
            value=Decimal("763500000"),
        )
        in_database = dict(stored, history_eligible=True)
        wanted = reported_fact(
            tag="NetIncomeLossAttributableToNoncontrollingInterest",
            value=Decimal("8800000"),
        )
        extracted = [
            wanted,
            dict(wanted),  # SEC lists the same fact once per frame.
            dict(wanted, accession="restated", filed=date(2022, 2, 1)),
            dict(wanted, period_start=date(2020, 10, 1), period_kind="quarterly"),
            stored,
        ]
        for fact in extracted:
            fact.pop("source_cik")
        task = SimpleNamespace(
            accession="original",
            start=date(2020, 1, 1),
            end=END,
            calendar={},
            cell_facts=[in_database],
        )

        with patch.object(
            version_reviews, "extract_facts", return_value=(extracted, [])
        ):
            version_reviews.add_supporting_facts([task], "0000320193", {}, None)

        self.assertEqual(len(task.supporting_facts), 1)
        attached = task.supporting_facts[0]
        self.assertEqual(attached["tag"], wanted["tag"])
        self.assertEqual(attached["source_cik"], "0000320193")
        self.assertTrue(attached["history_eligible"])


class ReviewedCashFlowScopeTests(unittest.TestCase):
    """Reviewed filings whose cash-flow statement shows only a continuing subtotal."""

    def continuing(self, value=Decimal("6261000000")):
        return {
            cash_flow.CONTINUING_TAG: {
                "tag": cash_flow.CONTINUING_TAG,
                "value": value,
                "label": "NET CASH PROVIDED BY OPERATING ACTIVITIES",
                "scope": "total",
            }
        }

    def test_explicit_zero_filing_keeps_the_whole_company_scope(self):
        """SLB's FY2016 primary document shows a dash for discontinued cash flow."""
        task = SimpleNamespace(cik="0000087347", accession="0001564590-17-000589")

        selected, scope, note = version_reviews.reviewed_cash_flow_scope(
            task, self.continuing(), True
        )

        self.assertEqual(selected[0]["value"], Decimal("6261000000"))
        self.assertEqual(scope, "total")
        self.assertIn("explicit zero", note)

    def test_not_material_filing_keeps_the_continuing_scope(self):
        """Citigroup's version shows the continuing amount, flagged as continuing."""
        task = SimpleNamespace(cik="0000831001", accession="0000831001-19-000027")

        _, scope, _ = version_reviews.reviewed_cash_flow_scope(
            task, self.continuing(), False
        )

        self.assertEqual(scope, "continuing")

    def test_unreviewed_or_contradicting_statement_is_not_approved(self):
        """Another filing, a nonzero discontinued operating part, or a discontinued
        row in a filing reviewed as having none, all stay unresolved."""
        slb = SimpleNamespace(cik="0000087347", accession="0001564590-17-000589")
        rtx = SimpleNamespace(cik="0000101829", accession="0000101829-20-000013")
        with_discontinued = self.continuing() | {
            cash_flow.DISCONTINUED_TAG: {
                "tag": cash_flow.DISCONTINUED_TAG,
                "value": Decimal("-233000000"),
                "scope": "discontinued",
            }
        }
        cases = (
            (
                SimpleNamespace(cik="0000087347", accession="other"),
                self.continuing(),
                True,
            ),
            (slb, with_discontinued, True),
            (rtx, self.continuing(), True),
        )
        for task, positions, has_discontinued in cases:
            with self.subTest(task=task):
                self.assertIsNone(
                    version_reviews.reviewed_cash_flow_scope(
                        task, positions, has_discontinued
                    )
                )


class StatementEndTests(unittest.TestCase):
    """Deere's FY2015 10-K labels the year ending Nov. 1, 2015 with "Oct. 31, 2015"."""

    def setUp(self):
        # The filing tags its facts with the nominal end date; a reviewed alias maps
        # that exact period to the canonical fiscal year.
        self.calendar = {
            "aliases": {
                "0000315189:2014-11-01:2015-10-31": {
                    "period_start": "2014-11-03",
                    "period_end": "2015-11-01",
                    "note": "Nominal Oct. 31 end of the 52-week year.",
                }
            }
        }
        self.fact = reported_fact(
            source_cik="0000315189",
            value=Decimal("1940000000"),
            period_start=date(2014, 11, 1),
            period_end=date(2015, 10, 31),
            filed=date(2015, 12, 18),
            history_eligible=True,
        )
        version = SimpleNamespace(
            metric="net_income",
            period_start=date(2014, 11, 3),
            period_end=date(2015, 11, 1),
            accession=self.fact["accession"],
            known_from=self.fact["filed"],
            form="10-K",
        )
        self.task = version_reviews.VersionTask(
            "DE", [self.fact], version, self.calendar
        )

    def review(self, header_date):
        html = statement([("NetIncomeLoss", "Net income", "$ 1,940")]).replace(
            "Dec. 31, 2020", header_date
        )
        statements = [({"url": "https://www.sec.gov/R2.htm"}, html)]
        summary = {"url": "https://www.sec.gov/FilingSummary.xml"}
        with patch.object(
            version_reviews, "income_statements", return_value=(summary, statements)
        ):
            return version_reviews.review_with_statement_end(self.task, {})

    def test_column_dated_as_the_filings_own_facts_is_accepted(self):
        """The canonical end fails, the filing's own aliased end matches its column."""
        self.assertEqual(
            self.task.statement_ends, [date(2015, 11, 1), date(2015, 10, 31)]
        )

        review = self.review("Oct. 31, 2015")

        self.assertEqual(review["value"], "1940000000")

    def test_unrelated_column_date_is_still_rejected(self):
        """The fallback is only the filing's own date, not any nearby date."""
        with self.assertRaises(ValueError):
            self.review("Oct. 25, 2015")


if __name__ == "__main__":
    unittest.main()
