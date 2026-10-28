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
        return version_reviews.review_net_income(task_with(fact), {})


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


if __name__ == "__main__":
    unittest.main()
