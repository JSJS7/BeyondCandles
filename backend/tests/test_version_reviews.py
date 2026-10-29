"""Check that an earlier filing's version is approved only from its own statement table."""

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from test_net_income import statement
from test_stocks_api import reported_fact

from app.data_pipeline import version_reviews

END = date(2020, 12, 31)


def task_with(fact):
    """A version of one net-income cell whose filing reported the given fact."""
    return SimpleNamespace(cik=fact["source_cik"], end=END, cell_facts=[fact])


def review_against(html, fact):
    statements = [({"url": "https://www.sec.gov/R4.htm"}, html)]
    summary = {"url": "https://www.sec.gov/FilingSummary.xml"}
    with patch.object(
        version_reviews, "income_statements", return_value=(summary, statements)
    ):
        return version_reviews.review_net_income(task_with(fact), {}, END)


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
