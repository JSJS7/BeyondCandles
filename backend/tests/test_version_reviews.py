"""Check that an earlier filing's version is approved only from its own statement table."""

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from test_eps import html_row
from test_eps import statement as eps_statement
from test_net_income import statement
from test_stocks_api import reported_fact

from app.data_pipeline import operating_cash_flow as cash_flow
from app.data_pipeline import version_reviews

END = date(2020, 12, 31)


def task_with(fact, supporting_facts=(), cik=None, accession="original"):
    """A version of one net-income cell whose filing reported the given fact.

    Without a fact, the filing stored nothing for the cell (only supporting facts).
    """
    return SimpleNamespace(
        cik=cik or fact["source_cik"],
        accession=accession,
        end=END,
        cell_facts=[] if fact is None else [fact],
        supporting_facts=list(supporting_facts),
    )


def review_against(html, fact, supporting_facts=(), **task_fields):
    statements = [({"url": "https://www.sec.gov/R4.htm"}, html)]
    summary = {"url": "https://www.sec.gov/FilingSummary.xml"}
    with patch.object(
        version_reviews, "income_statements", return_value=(summary, statements)
    ):
        return version_reviews.review_net_income(
            task_with(fact, supporting_facts, **task_fields), {}, END
        )


def annual_fact(tag, value, **changes):
    return reported_fact(
        tag=tag,
        value=Decimal(value),
        period_start=date(2020, 1, 1),
        period_end=END,
        **changes,
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


class ParentFactOffStatementTests(unittest.TestCase):
    """Tesla's FY2017 10-K tags NetIncomeLoss, but its statement shows that amount as
    "Net loss attributable to common stockholders" under another concept."""

    def setUp(self):
        self.group = annual_fact("ProfitLoss", "-2240000000")
        self.allocation = annual_fact(
            "NetIncomeLossAttributableToNoncontrollingInterest", "-279000000"
        )
        self.html = statement(
            [
                ("ProfitLoss", "Net loss", "(2,240)"),
                (
                    "NetIncomeLossAttributableToNoncontrollingInterest",
                    "Net loss attributable to noncontrolling interests",
                    "(279)",
                ),
                (
                    "NetIncomeLossAvailableToCommonStockholdersBasic",
                    "Net loss attributable to common stockholders",
                    "$ (1,961)",
                ),
            ]
        )

    def review(self, parent_value):
        parent = annual_fact("NetIncomeLoss", parent_value)
        task = task_with(parent, [self.allocation])
        task.cell_facts.append(self.group)
        statements = [({"url": "https://www.sec.gov/R4.htm"}, self.html)]
        summary = {"url": "https://www.sec.gov/FilingSummary.xml"}
        with patch.object(
            version_reviews, "income_statements", return_value=(summary, statements)
        ):
            return version_reviews.review_net_income(task, {}, END)

    def test_reconciliation_equal_to_the_parent_fact_is_approved(self):
        """Group loss less noncontrolling interests (-2 240 + 279) equals the fact."""
        review = self.review("-1961000000")

        self.assertEqual(review["value"], "-1961000000")
        tags = [component["fact"]["tag"] for component in review["components"]]
        self.assertEqual(
            tags, ["ProfitLoss", "NetIncomeLossAttributableToNoncontrollingInterest"]
        )

    def test_reconciliation_differing_from_the_parent_fact_is_rejected(self):
        """The statement reconciliation does not replace a parent fact it contradicts."""
        with self.assertRaisesRegex(ValueError, "differs from the parent fact"):
            self.review("-1900000000")


class CommonRowOnlyTests(unittest.TestCase):
    """AbbVie's FY2015 10-K tags its "Net earnings" row only with the common-shareholder
    concept, which the import did not store for that period."""

    def setUp(self):
        self.common = annual_fact(
            "NetIncomeLossAvailableToCommonStockholdersBasic", "5144000000"
        )
        self.html = statement(
            [
                (
                    "NetIncomeLossAvailableToCommonStockholdersBasic",
                    "Net earnings",
                    "$ 5,144",
                )
            ]
        )

    def review_task(self, supporting_facts):
        task = task_with(None, supporting_facts, cik=self.common["source_cik"])
        task.metric = "net_income"
        task.statement_ends = [END]
        statements = [({"url": "https://www.sec.gov/R4.htm"}, self.html)]
        summary = {"url": "https://www.sec.gov/FilingSummary.xml"}
        with patch.object(
            version_reviews, "income_statements", return_value=(summary, statements)
        ):
            return version_reviews.review_task(task, {"net_income": {}})

    def test_sole_net_earnings_row_is_approved_and_pinned(self):
        """No stored fact, but the filing's own fact matches its only net-income row."""
        review, reason = self.review_task([self.common])

        self.assertIsNone(reason)
        self.assertEqual(review["value"], "5144000000")
        self.assertTrue(review["components"][0]["statement_position"])

    def test_version_without_any_fact_of_the_filing_stays_unresolved(self):
        """Without stored or supporting facts, net income is not reviewed at all."""
        review, reason = self.review_task([])

        self.assertIsNone(review)
        self.assertEqual(reason, "The filing has no stored fact for this cell")


class ContinuingParentRowTests(unittest.TestCase):
    """AT&T's FY2016 10-K shows "Net Income Attributable to AT&T" under the
    continuing-operations concept; the company had no discontinued operations."""

    CIK = "0000732717"
    ACCESSION = "0000732717-17-000021"

    def setUp(self):
        self.facts = [
            annual_fact(version_reviews.CONTINUING_GROUP_TAG, "13333000000"),
            annual_fact(
                "NetIncomeLossAttributableToNoncontrollingInterest", "357000000"
            ),
            annual_fact(version_reviews.CONTINUING_PARENT_TAG, "12976000000"),
        ]
        self.rows = [
            (version_reviews.CONTINUING_GROUP_TAG, "Net Income", "13,333"),
            (
                "NetIncomeLossAttributableToNoncontrollingInterest",
                "Less: Net Income Attributable to Noncontrolling Interest",
                "(357)",
            ),
            (
                version_reviews.CONTINUING_PARENT_TAG,
                "Net Income Attributable to AT&T",
                "$ 12,976",
            ),
        ]

    def review(self, rows, accession=ACCESSION):
        return review_against(
            statement(rows), None, self.facts, cik=self.CIK, accession=accession
        )

    def test_reviewed_filing_is_approved_from_the_parent_row(self):
        """13 333 - 357 = 12 976, the row's own fact, pinned from the statement."""
        review = self.review(self.rows)

        self.assertEqual(review["value"], "12976000000")
        self.assertEqual(review["selected_tag"], version_reviews.CONTINUING_PARENT_TAG)
        self.assertTrue(review["components"][0]["statement_position"])

    def test_other_filing_or_discontinued_operations_stay_unresolved(self):
        """The mapping holds for the reviewed filing only, and never beside a
        discontinued-operations row."""
        discontinued = (
            "IncomeLossFromDiscontinuedOperationsNetOfTax",
            "Income from discontinued operations",
            "0",
        )
        cases = (
            (self.rows, "another"),
            (self.rows[:2] + [discontinued] + self.rows[2:], self.ACCESSION),
        )
        for rows, accession in cases:
            with self.subTest(accession=accession):
                with self.assertRaises(ValueError):
                    self.review(rows, accession)


class CustomRevenueTests(unittest.TestCase):
    """American Express' FY2015 total is a custom axp concept, which SEC companyfacts
    omit; the reviewed total is read from the filing's original instance."""

    def setUp(self):
        self.fact = reported_fact(
            source_cik="0000004962",
            metric="revenue",
            taxonomy="axp",
            tag="TotalRevenuesNetOfInterestExpense",
            value=Decimal("32818000000"),
            period_start=date(2020, 1, 1),
            period_end=END,
        )
        self.rule = {
            "taxonomy": "axp",
            "tag": "TotalRevenuesNetOfInterestExpense",
            "label": "Total revenues net of interest expense",
            "value": "32818000000",
            "summary": {"url": "https://www.sec.gov/FilingSummary.xml"},
            "evidence": {"url": "https://www.sec.gov/R2.htm"},
            "original_instance": {"url": "https://www.sec.gov/axp.xml"},
        }

    def review(self, cell_facts):
        task = SimpleNamespace(
            ticker="AXP",
            cik="0000004962",
            accession="original",
            start=date(2020, 1, 1),
            end=END,
            source="0000004962:original",
            filing_facts=cell_facts,
            cell_facts=cell_facts,
        )
        evidence = {task.source: {"summary": self.rule["summary"]}}
        with (
            patch.object(
                version_reviews, "verify_filing_period", return_value=(self.rule, [])
            ),
            patch.object(
                version_reviews, "custom_revenue_facts", return_value=[self.fact]
            ) as instance,
        ):
            review = version_reviews.review_revenue(task, evidence, END)
        return review, instance

    def test_unstored_custom_total_is_pinned_from_the_instance(self):
        """No stored fact: the instance fact is used and pinned with its file."""
        review, _ = self.review([])

        component = review["components"][0]
        self.assertEqual(review["value"], "32818000000")
        self.assertTrue(component["instance_position"])
        self.assertEqual(component["instance"], self.rule["original_instance"])

    def test_stored_custom_total_keeps_the_database_fact(self):
        """A custom fact the import stored is not re-read or pinned from the instance."""
        review, instance = self.review([self.fact])

        self.assertNotIn("instance_position", review["components"][0])
        instance.assert_not_called()


class BerkshireClassBFootnoteTests(unittest.TestCase):
    """Berkshire's 10-K for 2015 shows EPS only per Class A share (14,656) and states
    the Class B amount (1/1,500, $9.77) in the statement's footnote, without a fact."""

    def review(self, stated, accession="0001193125-16-483023"):
        rows = html_row("StatementClassOfStockAxis", "Class A [Member]", "")
        rows += html_row(
            "EarningsPerShareBasic",
            "Net earnings per share attributable to Berkshire Hathaway shareholders",
            "$ 14,656",
        )
        footnote = (
            "<div>[1] Equivalent Class B shares outstanding are 1,500 times the "
            "equivalent Class A amount. Net earnings per equivalent Class B share "
            "outstanding are one-fifteen-hundredth (1/1,500) of the equivalent Class A "
            f"amount or ${stated} for 2020, $8.06 for 2019 and $7.90 for 2018. </div>"
        )
        task = SimpleNamespace(
            metric="eps_diluted",
            cik=version_reviews.BERKSHIRE_CIK,
            accession=accession,
            source=f"{version_reviews.BERKSHIRE_CIK}:{accession}",
            start=date(2020, 1, 1),
            end=END,
            filed=date(2021, 2, 27),
            form="10-K",
            cell_facts=[],
        )
        statements = [
            ({"url": "https://www.sec.gov/R3.htm"}, eps_statement(rows) + footnote)
        ]
        summary = {"url": "https://www.sec.gov/FilingSummary.xml"}
        with patch.object(
            version_reviews, "income_statements", return_value=(summary, statements)
        ):
            return version_reviews.review_eps(task, {"income": {}, "eps": {}}, END)

    def test_stated_amount_equal_to_class_a_over_1500_is_pinned(self):
        """14,656 / 1,500 = 9.7707 rounds to the stated 9.77, so 9.77 is approved."""
        review = self.review("9.77")

        component = review["components"][0]
        self.assertEqual(review["value"], "9.77")
        self.assertEqual(review["eps_basis"], "basic")
        self.assertTrue(component["statement_position"])
        self.assertEqual(component["fact"]["accession"], "0001193125-16-483023")

    def test_stated_amount_differing_from_the_calculation_is_rejected(self):
        """A footnote amount that is not Class A EPS / 1,500 is not trusted."""
        with self.assertRaises(ValueError):
            self.review("9.78")

    def test_other_berkshire_filings_keep_the_class_b_row_review(self):
        """Only the two reviewed filings use the footnote; others need a Class B row."""
        with (
            patch.object(version_reviews, "instance_candidates", return_value=[]),
            self.assertRaises(ValueError),
        ):
            self.review("9.77", accession="0001193125-18-057033")


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


class AlternativeEquityJobTests(unittest.TestCase):
    def test_missing_statements_and_instance_of_the_filing_are_requested(self):
        """As review-equity escalates: an unresolved version filed soon after the year
        end has only its balance sheet cached; its equity statement and instance are
        requested, while a filing with everything cached needs no download."""
        partial = SimpleNamespace(source="0000702165:0000702165-17-000018")
        complete = SimpleNamespace(source="0000702165:0000702165-18-000011")
        evidence = {
            partial.source: {"balance_files": ["R4.htm"]},
            complete.source: {
                "balance_files": ["R4.htm"],
                "equity_files": ["R7.htm"],
                "instance": {"url": "https://www.sec.gov/nsc.xml"},
            },
        }

        jobs = version_reviews.alternative_equity_jobs([partial, complete], evidence)

        self.assertEqual(
            jobs, {(partial.source, "equity"), (partial.source, "instance")}
        )


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


class MissingEquityPeriodTests(unittest.TestCase):
    def test_version_without_the_annual_period_stays_unresolved(self):
        """An equity version whose filing facts do not form the period is one issue.

        It must not raise StopIteration, which would stop the whole review instead
        of leaving this single version unresolved.
        """
        fact = annual_fact("StockholdersEquity", "100")
        task = SimpleNamespace(
            metric="equity",
            cik=fact["source_cik"],
            cell_facts=[fact],
            supporting_facts=[],
            known_facts=[],
            start=date(2020, 1, 1),
            end=END,
            filed=date(2021, 2, 1),
            statement_ends=[END],
        )

        review, reason = version_reviews.review_task(task, {"equity": {}})

        self.assertIsNone(review)
        self.assertEqual(reason, "The filing's facts do not form this annual period")


if __name__ == "__main__":
    unittest.main()
