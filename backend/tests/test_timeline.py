"""Filings that date earlier versions of a cell must never define today's value."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from test_stocks_api import reported_fact

from app.annual_financials import METRIC_NAMES, annual_periods
from app.data_pipeline.revenue_facts import instance_balances
from app.data_pipeline.sec_history import history_eligible
from app.metric_history import company_versions, periods_as_of
from app.schemas.stock import AnnualMetric
from app.timeline import as_known_then, on_timeline, timeline_facts, within_bounds

PRIMARY = "0000320193"
PREDECESSOR = "0000000042"
RULES = {
    "issuers": {
        PREDECESSOR: {
            "ticker": "AAPL",
            "filed_to": "2021-12-31",
            "period_end_to": "2020-12-31",
            "note": "Not a bound.",
        }
    },
    "filings": [{"cik": PRIMARY, "accession": "missing-from-companyfacts"}],
}


def predecessor_fact(**changes):
    """A report the current view excludes: the successor reports this period again."""
    return reported_fact(
        source_cik=PREDECESSOR,
        accession="predecessor",
        history_eligible=False,
        **changes,
    )


def successor_fact(**changes):
    return reported_fact(
        filed=date(2022, 2, 1),
        accession="successor",
        history_eligible=True,
        **changes,
    )


class BoundsTests(unittest.TestCase):
    def test_bounds_ignore_descriptive_fields(self):
        """Regression: the ticker and note of a rule are not date bounds."""
        fact = predecessor_fact()

        self.assertTrue(within_bounds(fact, RULES["issuers"][PREDECESSOR]))

    def test_filed_and_period_bounds_are_separate(self):
        """Either a later filing or a later period falls outside the rule."""
        later_filing = predecessor_fact(filed=date(2022, 1, 1))
        later_period = predecessor_fact(period_end=date(2021, 12, 31))
        bounds = RULES["issuers"][PREDECESSOR]

        self.assertFalse(within_bounds(later_filing, bounds))
        self.assertFalse(within_bounds(later_period, bounds))


class SelectionTests(unittest.TestCase):
    def test_only_excluded_facts_within_a_rule_are_on_the_timeline(self):
        """Current-view facts and unknown issuers do not qualify."""
        unknown = dict(predecessor_fact(), source_cik="0000000099")

        self.assertTrue(on_timeline(predecessor_fact(), RULES))
        self.assertFalse(on_timeline(successor_fact(), RULES))
        self.assertFalse(on_timeline(unknown, RULES))

    def test_listed_filing_of_the_primary_issuer_qualifies(self):
        """SPGI: a 10-K missing from companyfacts dates its period like a predecessor."""
        fact = reported_fact(
            accession="missing-from-companyfacts", history_eligible=False
        )

        self.assertTrue(on_timeline(fact, RULES))

    def test_as_known_then_marks_only_timeline_facts(self):
        """A reader of that day sees the predecessor's fact as usable, others unchanged."""
        excluded = predecessor_fact(filed=date(2022, 1, 1))

        marked = as_known_then([predecessor_fact(), excluded], RULES)

        self.assertTrue(marked[0]["history_eligible"])
        self.assertFalse(marked[1]["history_eligible"])

    def test_source_outside_the_current_view_is_not_eligible(self):
        """A predecessor registered only for dating cannot enter today's selection."""
        history = {
            "status": "reviewed",
            "predecessors": [{"cik": PREDECESSOR, "current_view": False}],
        }

        self.assertFalse(history_eligible(predecessor_fact(), PRIMARY, history))

    def test_listed_filing_is_never_eligible(self):
        """A filing read for dating must not become the latest report of earlier years."""
        fact = reported_fact(accession="missing-from-companyfacts")

        with patch("app.data_pipeline.sec_history.timeline_rules", return_value=RULES):
            self.assertFalse(history_eligible(fact, PRIMARY, None))


class VersionTests(unittest.TestCase):
    def versions(self, facts):
        with patch("app.metric_history.timeline_rules", return_value=RULES):
            return company_versions(
                "AAPL", facts, {"actions": []}, {"aliases": {}}, reviews={}
            )

    def test_period_appears_on_the_predecessors_filing_date(self):
        """Regression for W18: the first version comes from the earlier report."""
        versions = self.versions([predecessor_fact(), successor_fact()])
        income = [version for version in versions if version.metric == "net_income"]

        self.assertEqual(income[0].known_from, date(2021, 2, 1))
        self.assertEqual(income[0].accession, "predecessor")

    def test_timeline_fact_alone_creates_no_period(self):
        """Today's table comes from the current view: dating facts add no period."""
        self.assertEqual(self.versions([predecessor_fact()]), [])

    def test_timeline_facts_do_not_change_todays_value(self):
        """The current selection reads only eligible facts, whatever predates them."""
        today = annual_periods([successor_fact()])
        with_predecessor = annual_periods([predecessor_fact(), successor_fact()])

        self.assertEqual(today, with_predecessor)

    def test_as_of_serves_a_period_known_only_from_a_predecessor(self):
        """Regression for W18: the variants lookup needs these facts too."""
        fact = dict(predecessor_fact())
        details = AnnualMetric(status="needs_review", variants=[])
        stored = [
            {
                "period_start": date(2020, 1, 1),
                "period_end": date(2020, 12, 31),
                "metric": metric,
                "known_from": date(2021, 2, 1),
                "details": details.model_dump(mode="json"),
            }
            for metric in METRIC_NAMES
        ]

        with patch("app.metric_history.timeline_rules", return_value=RULES):
            periods = periods_as_of(
                [fact, successor_fact()],
                stored,
                date(2021, 6, 1),
                period_rules={"aliases": {}},
            )

        self.assertEqual(
            [period.period_end for period in periods], [date(2020, 12, 31)]
        )


class InstanceTests(unittest.TestCase):
    INSTANCE = b"""<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
      <context id="whole">
        <entity><identifier scheme="cik">0000064040</identifier></entity>
        <period><instant>2024-12-31</instant></period>
      </context>
      <context id="part">
        <entity><identifier scheme="cik">0000064040</identifier>
          <segment><xbrldi:explicitMember dimension="d">m</xbrldi:explicitMember></segment>
        </entity>
        <period><instant>2024-12-31</instant></period>
      </context>
      <unit id="usd"><measure>iso4217:USD</measure></unit>
      <us-gaap:StockholdersEquity contextRef="whole" unitRef="usd">%s</us-gaap:StockholdersEquity>
      <us-gaap:StockholdersEquity contextRef="whole" unitRef="usd">33159</us-gaap:StockholdersEquity>
      <us-gaap:StockholdersEquity contextRef="part" unitRef="usd">1</us-gaap:StockholdersEquity>
    </xbrl>"""
    METADATA = {
        "accession": "a",
        "filed": date(2025, 2, 11),
        "form": "10-K",
        "fiscal_period": "FY",
    }

    def read(self, first_value):
        body = self.INSTANCE % first_value
        return instance_balances(
            body, "0000064040", self.METADATA, ("StockholdersEquity",)
        )

    def test_repeated_balance_counts_once_and_segments_are_ignored(self):
        """An instance repeats a balance by statement; a segment is not the company."""
        facts = self.read(b"33159")

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["value"], Decimal(33159))
        self.assertEqual(facts[0]["period_kind"], "instant")

    def test_conflicting_balances_stop_the_read(self):
        """Two consolidated values of one balance cannot be resolved silently."""
        with self.assertRaises(ValueError):
            self.read(b"1")

    def test_timeline_facts_of_a_mixed_list(self):
        """Only the facts a rule covers are added to the dating facts."""
        facts = [predecessor_fact(), successor_fact()]

        self.assertEqual(timeline_facts(facts, RULES), [facts[0]])


if __name__ == "__main__":
    unittest.main()
