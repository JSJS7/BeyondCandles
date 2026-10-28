"""Verify the point-in-time versions of annual cells on known filing histories.

Values and dates are the real SEC facts of each case (checked in the database
on 28.10.2026); only the issuer-independent structure is simplified.
"""

import unittest
from collections import defaultdict
from datetime import date
from decimal import Decimal

from test_stocks_api import reported_fact

from app.eps_splits import LATEST_VIEW_NOTE, apply_share_basis
from app.metric_history import (
    ReportDate,
    cell_versions,
    differs_only_by_rounding,
    source_key,
)
from app.schemas.stock import AnnualMetric, FilingValue

NVDA_SPLITS = {
    "basis_date": "2026-10-13",
    "actions": [
        {
            "ticker": "NVDA",
            "date": "2021-07-20",
            "new_shares": 4,
            "old_shares": 1,
            "source_url": "https://www.sec.gov/example-4-for-1",
        },
        {
            "ticker": "NVDA",
            "date": "2024-06-10",
            "new_shares": 10,
            "old_shares": 1,
            "source_url": "https://www.sec.gov/example-10-for-1",
        },
    ],
}


def approved_metric(*sources, value=None, calculation=None):
    """Today's approved selection pointing at the given facts."""
    filing_values = [FilingValue.model_validate(source) for source in sources]
    if value is None:
        value = filing_values[0].value
    return AnnualMetric(
        status="ready",
        value=value,
        variants=filing_values,
        selected_sources=filing_values,
        calculation=calculation,
        selection_note="Approved.",
    )


def build(facts, raw, metric, final=None, eps_context=None):
    """Run the cell history for facts of one cell; the first 10-K opens the period."""
    first = min(
        (fact for fact in facts if fact["form"] == "10-K"),
        key=lambda fact: fact["filed"],
    )
    facts_by_key = defaultdict(list)
    for fact in facts:
        facts_by_key[source_key(fact)].append(fact)
    return cell_versions(
        metric,
        facts[0]["period_start"],
        facts[0]["period_end"],
        raw,
        final or raw,
        facts,
        facts_by_key,
        ReportDate(first["filed"], first["accession"], first["form"], annual=True),
        eps_context,
    )


def timeline(versions):
    return [
        (version.known_from, version.value.status, version.confirmation)
        for version in versions
    ]


class AmdRevisionTests(unittest.TestCase):
    """AMD FY2017 net income: 43 mln first, -33 mln after the 2019 restatement."""

    def setUp(self):
        period = {
            "source_cik": "0000002488",
            "period_start": date(2017, 1, 1),
            "period_end": date(2017, 12, 30),
        }
        self.first = reported_fact(
            **period,
            value=Decimal("43000000"),
            filed=date(2018, 2, 27),
            accession="0000002488-18-000042",
        )
        self.restated = reported_fact(
            **period,
            value=Decimal("-33000000"),
            filed=date(2019, 2, 8),
            accession="0000002488-19-000011",
        )
        self.latest = reported_fact(
            **period,
            value=Decimal("-33000000"),
            filed=date(2020, 2, 4),
            accession="0000002488-20-000008",
        )

    def test_changed_first_version_needs_review(self):
        """43 mln is not today's value, so without a table check it is never served."""
        facts = [self.first, self.restated, self.latest]

        versions = build(facts, approved_metric(self.latest), "net_income")

        self.assertEqual(
            timeline(versions),
            [
                (date(2018, 2, 27), "needs_review", None),
                (date(2019, 2, 8), "ready", "identical"),
                (date(2020, 2, 4), "ready", "approved"),
            ],
        )
        self.assertIsNone(versions[0].value.value)
        self.assertEqual(versions[1].value.value, Decimal("-33000000"))
        self.assertEqual(
            versions[1].value.selected_sources[0].accession, "0000002488-19-000011"
        )


class NvdaSplitTests(unittest.TestCase):
    """NVDA FY2021 EPS: 6.90 before the 4:1 split, later restated as 1.73."""

    def test_split_and_cent_rounding_is_not_a_revision(self):
        """6.90 / 40 = 0.1725 and 1.73 / 10 = 0.173 describe the same EPS."""
        period = {
            "source_cik": "0001045810",
            "metric": "eps_diluted",
            "tag": "EarningsPerShareDiluted",
            "unit": "USD/shares",
            "period_start": date(2020, 1, 27),
            "period_end": date(2021, 1, 31),
        }
        first = reported_fact(
            **period,
            value=Decimal("6.90"),
            filed=date(2021, 2, 26),
            accession="0001045810-21-000010",
        )
        restated = reported_fact(
            **period,
            value=Decimal("1.73"),
            filed=date(2022, 3, 18),
            accession="0001045810-22-000036",
        )
        latest = reported_fact(
            **period,
            value=Decimal("1.73"),
            filed=date(2023, 2, 24),
            accession="0001045810-23-000017",
        )
        raw = approved_metric(latest)
        final = raw.model_copy(deep=True)
        apply_share_basis(final, "NVDA", NVDA_SPLITS, LATEST_VIEW_NOTE)

        versions = build(
            [first, restated, latest],
            raw,
            "eps_diluted",
            final=final,
            eps_context=("NVDA", NVDA_SPLITS),
        )

        self.assertEqual(
            timeline(versions),
            [
                (date(2021, 2, 26), "ready", "split"),
                (date(2022, 3, 18), "ready", "identical"),
                (date(2023, 2, 24), "ready", "approved"),
            ],
        )
        # Each version keeps its reported value and only later splits are applied.
        first_version = versions[0].value
        self.assertEqual(first_version.eps_share_basis.reported_value, Decimal("6.90"))
        self.assertEqual(first_version.value, Decimal("0.1725000000"))
        self.assertEqual(len(first_version.eps_share_basis.applied_splits), 2)
        self.assertEqual(versions[1].value.value, Decimal("0.1730000000"))


class RoundingTests(unittest.TestCase):
    """TSLA 2018 revenue: 21 461 268 thousand first, 21 461 million later."""

    def setUp(self):
        self.period = {
            "source_cik": "0001318605",
            "metric": "revenue",
            "tag": "Revenues",
            "period_start": date(2018, 1, 1),
            "period_end": date(2018, 12, 31),
        }
        self.latest = reported_fact(
            **self.period,
            value=Decimal("21461000000"),
            filed=date(2021, 2, 8),
            accession="0001564590-21-004599",
        )

    def first(self, value):
        return reported_fact(
            **self.period,
            value=Decimal(value),
            filed=date(2019, 2, 19),
            accession="0001564590-19-003165",
        )

    def test_coarser_rounding_keeps_the_more_precise_first_value(self):
        """The first version is confirmed and served with its own, more precise value."""
        first = self.first("21461268000")

        versions = build([first, self.latest], approved_metric(self.latest), "revenue")

        self.assertEqual(versions[0].confirmation, "rounding")
        self.assertEqual(versions[0].value.value, Decimal("21461268000"))

    def test_difference_beyond_rounding_needs_review(self):
        """21 461 600 thousand rounds to 21 462 million, so it is a changed value."""
        first = self.first("21461600000")

        versions = build([first, self.latest], approved_metric(self.latest), "revenue")

        self.assertEqual(versions[0].value.status, "needs_review")

    def test_same_precision_values_must_be_equal(self):
        """Regression: one unit apart at equal precision is a revision, not rounding."""
        self.assertFalse(
            differs_only_by_rounding(Decimal("-0.62"), Decimal("-0.63"), True)
        )
        self.assertFalse(
            differs_only_by_rounding(
                Decimal("21461000000"), Decimal("21462000000"), False
            )
        )


class QuarterlyVersionTests(unittest.TestCase):
    """AAPL equity on 28.09.2024: one value repeated by a 10-K and later 10-Qs."""

    def setUp(self):
        self.period = {
            "source_cik": "0000320193",
            "metric": "equity",
            "tag": "StockholdersEquity",
            "period_kind": "instant",
            "period_start": date(2024, 9, 28),
            "period_end": date(2024, 9, 28),
            "value": Decimal("56950000000"),
        }
        self.annual = reported_fact(
            **self.period, filed=date(2024, 11, 1), accession="0000320193-24-000123"
        )
        self.latest = reported_fact(
            **self.period,
            form="10-Q",
            filed=date(2026, 7, 31),
            accession="0000320193-26-000020",
        )

    def test_confirmed_quarterly_versions_keep_their_own_source(self):
        """On 31.12.2024 the source is the 10-K, not a 10-Q filed later."""
        quarter = reported_fact(
            **self.period,
            form="10-Q",
            filed=date(2025, 1, 31),
            accession="0000320193-25-000008",
        )

        versions = build(
            [self.annual, quarter, self.latest], approved_metric(self.latest), "equity"
        )

        self.assertEqual(
            [(v.known_from, v.form, v.confirmation) for v in versions],
            [
                (date(2024, 11, 1), "10-K", "identical"),
                (date(2025, 1, 31), "10-Q", "identical"),
                (date(2026, 7, 31), "10-Q", "approved"),
            ],
        )
        self.assertEqual(
            versions[0].value.selected_sources[0].accession, "0000320193-24-000123"
        )

    def test_unconfirmed_quarterly_version_is_not_recorded(self):
        """A differing 10-Q leaves the previous version in force until the next report."""
        changed = dict(self.period, value=Decimal("56000000000"))
        quarter = reported_fact(
            **changed,
            form="10-Q",
            filed=date(2025, 1, 31),
            accession="0000320193-25-000008",
        )

        versions = build(
            [self.annual, quarter, self.latest], approved_metric(self.latest), "equity"
        )

        self.assertEqual(
            [v.known_from for v in versions], [date(2024, 11, 1), date(2026, 7, 31)]
        )


class StatementPositionTests(unittest.TestCase):
    """CAT 2020 equity: group equity 15 378 minus noncontrolling interests 47."""

    def test_statement_only_component_needs_review_before_its_report(self):
        """NCI is read only from the 2024 table, so earlier 10-Ks need review."""
        period = {
            "source_cik": "0000018230",
            "metric": "equity",
            "tag": "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "period_kind": "instant",
            "period_start": date(2020, 12, 31),
            "period_end": date(2020, 12, 31),
            "value": Decimal("15378000000"),
        }
        first = reported_fact(
            **period, filed=date(2021, 2, 17), accession="0000018230-21-000063"
        )
        quarter = reported_fact(
            **period,
            form="10-Q",
            filed=date(2021, 5, 5),
            accession="0000018230-21-000151",
        )
        latest = reported_fact(
            **period, filed=date(2024, 2, 16), accession="0000018230-24-000009"
        )
        noncontrolling = dict(
            latest,
            value=Decimal("47000000"),
            statement_label="Beginning balance at Dec. 31, 2020",
            statement_column="Noncontrolling interests",
            statement_url="https://www.sec.gov/example/R6.htm",
        )
        raw = approved_metric(
            latest,
            noncontrolling,
            value=Decimal("15331000000"),
            calculation="Group equity - noncontrolling interests",
        )

        versions = build([first, quarter, latest], raw, "equity")

        self.assertEqual(
            timeline(versions),
            [
                (date(2021, 2, 17), "needs_review", None),
                (date(2024, 2, 16), "ready", "approved"),
            ],
        )
        # Group equity must never be served as the parent's equity.
        self.assertIsNone(versions[0].value.value)
        self.assertEqual(versions[-1].value.value, Decimal("15331000000"))


class UnapprovedCellTests(unittest.TestCase):
    def test_cell_without_approved_value_has_one_version(self):
        """Today's needs_review cell is copied once, from the period's first report."""
        fact = reported_fact()
        raw = AnnualMetric(
            status="needs_review",
            variants=[FilingValue.model_validate(fact)],
            selection_note="Conflict.",
        )

        versions = build([fact], raw, "net_income")

        self.assertEqual(timeline(versions), [(date(2021, 2, 1), "needs_review", None)])
        self.assertEqual(versions[0].value.variants, [])


if __name__ == "__main__":
    unittest.main()
