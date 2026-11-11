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
from app.fact_identity import fact_identity
from app.metric_history import (
    ReportDate,
    cell_versions,
    differs_only_by_rounding,
    source_key,
    split_scale,
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


def build(
    facts, raw, metric, final=None, eps_context=None, reviews=None, keep_repeats=False
):
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
        reviews,
        keep_repeats,
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

    def statement_review(self, value):
        """A version review of the first 10-K, as review-versions pins it."""
        return {
            self.first["accession"]: {
                "value": value,
                "components": [{"fact": fact_identity(self.first), "coefficient": 1}],
                "selected_tag": "NetIncomeLoss",
                "calculation": None,
                "note": "Checked.",
            }
        }

    def test_reviewed_first_version_serves_its_own_value(self):
        """After a table check of the 2018 10-K, 43 mln is served until the restatement."""
        facts = [self.first, self.restated, self.latest]
        reviews = self.statement_review("43000000")

        versions = build(
            facts, approved_metric(self.latest), "net_income", reviews=reviews
        )

        self.assertEqual(
            timeline(versions)[0], (date(2018, 2, 27), "ready", "reviewed")
        )
        self.assertEqual(versions[0].value.value, Decimal("43000000"))
        self.assertEqual(
            versions[0].value.selected_sources[0].accession, "0000002488-18-000042"
        )

    def test_review_from_the_original_instance_needs_no_stored_fact(self):
        """A value read from the filing's original XBRL is pinned by the review.

        Without the flag the same untagged value would be rejected as unknown.
        """
        # The 2018 10-K opens the period but has no stored fact for this cell.
        facts = [self.restated, self.latest]
        facts_by_key = defaultdict(list)
        for fact in facts:
            facts_by_key[source_key(fact)].append(fact)
        first_report = ReportDate(
            self.first["filed"], self.first["accession"], "10-K", annual=True
        )
        reviews = self.statement_review("43000000")
        component = reviews[self.first["accession"]]["components"][0]

        def versions():
            raw = approved_metric(self.latest)
            return cell_versions(
                "net_income",
                self.first["period_start"],
                self.first["period_end"],
                raw,
                raw,
                facts,
                facts_by_key,
                first_report,
                reviews=reviews,
            )

        rejected = versions()
        component["instance_position"] = True
        accepted = versions()

        self.assertEqual(timeline(rejected)[0][1], "needs_review")
        self.assertEqual(
            timeline(accepted)[0], (date(2018, 2, 27), "ready", "reviewed")
        )

    def test_review_of_changed_fact_is_not_used(self):
        """A review pinned to another value no longer describes the stored fact."""
        facts = [self.first, self.restated, self.latest]
        reviews = self.statement_review("42000000")
        reviews[self.first["accession"]]["components"][0]["fact"]["value"] = "42000000"

        versions = build(
            facts, approved_metric(self.latest), "net_income", reviews=reviews
        )

        self.assertEqual(
            timeline(versions)[0], (date(2018, 2, 27), "needs_review", None)
        )
        self.assertIn("no longer matches", versions[0].value.selection_note)

    def test_every_unchecked_report_is_listed_for_review(self):
        """A repeated old value in the next 10-K is a separate report to check.

        Merged, only the first report would be reviewed, and after its approval
        the repeat would reappear as an unchecked version.
        """
        repeat = dict(
            self.first, filed=date(2018, 10, 1), accession="0000002488-18-000099"
        )
        facts = [self.first, repeat, self.restated, self.latest]

        merged = build(facts, approved_metric(self.latest), "net_income")
        listed = build(
            facts, approved_metric(self.latest), "net_income", keep_repeats=True
        )

        self.assertEqual(timeline(merged)[0], (date(2018, 2, 27), "needs_review", None))
        self.assertEqual(timeline(merged)[1][0], date(2019, 2, 8))
        self.assertEqual(
            [entry[:2] for entry in timeline(listed)[:2]],
            [
                (date(2018, 2, 27), "needs_review"),
                (date(2018, 10, 1), "needs_review"),
            ],
        )


class NvdaSplitTests(unittest.TestCase):
    """NVDA FY2021 EPS: 6.90 before the 4:1 split, later restated as 1.73."""

    def test_report_filed_on_the_split_date_already_uses_the_new_share_count(self):
        """K11: as in apply_share_basis, only splits after the filing date restate a
        report's EPS; a report filed on the split date needs no 10:1 restatement."""
        filed_day_before = split_scale("NVDA", date(2024, 6, 7), policy=NVDA_SPLITS)
        filed_on_split_day = split_scale("NVDA", date(2024, 6, 10), policy=NVDA_SPLITS)

        self.assertEqual(filed_day_before, Decimal("0.1"))
        self.assertEqual(filed_on_split_day, Decimal(1))

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


class GeReviewedSplitTests(unittest.TestCase):
    """GE FY2016 EPS: 0.89 in the 2017 10-K, restated to 0.75 in 2019."""

    def test_reviewed_version_receives_the_later_reverse_split(self):
        """A table-checked old value is restated to today's share basis.

        GE's 1-for-8 reverse split (2 Aug 2021) postdates the 2017 10-K, so its
        0.89 becomes 0.89 × 8 = 7.12, comparable with the latest 0.75 × 8 = 6.00.
        Served unadjusted, the old version would look like a 85% drop.
        """
        period = {
            "source_cik": "0000040545",
            "metric": "eps_diluted",
            "tag": "EarningsPerShareDiluted",
            "unit": "USD/shares",
            "period_start": date(2016, 1, 1),
            "period_end": date(2016, 12, 31),
        }
        first = reported_fact(
            **period,
            value=Decimal("0.89"),
            filed=date(2017, 2, 24),
            accession="0000040545-17-000010",
        )
        latest = reported_fact(
            **period,
            value=Decimal("0.75"),
            filed=date(2019, 2, 26),
            accession="0000040545-19-000014",
        )
        reverse_split = {
            "basis_date": "2026-10-13",
            "actions": [
                {
                    "ticker": "GE",
                    "date": "2021-08-02",
                    "new_shares": 1,
                    "old_shares": 8,
                    "source_url": "https://www.sec.gov/example-1-for-8",
                }
            ],
        }
        reviews = {
            first["accession"]: {
                "value": "0.89",
                "components": [{"fact": fact_identity(first), "coefficient": 1}],
                "selected_tag": "EarningsPerShareDiluted",
                "calculation": None,
                "eps_basis": "diluted",
                "note": "Checked.",
            }
        }
        raw = approved_metric(latest)
        final = raw.model_copy(deep=True)
        apply_share_basis(final, "GE", reverse_split, LATEST_VIEW_NOTE)

        versions = build(
            [first, latest],
            raw,
            "eps_diluted",
            final=final,
            eps_context=("GE", reverse_split),
            reviews=reviews,
        )

        self.assertEqual(
            timeline(versions),
            [
                (date(2017, 2, 24), "ready", "reviewed"),
                (date(2019, 2, 26), "ready", "approved"),
            ],
        )
        reviewed = versions[0].value
        self.assertEqual(reviewed.value, Decimal("7.1200000000"))
        self.assertEqual(reviewed.eps_share_basis.reported_value, Decimal("0.89"))
        self.assertEqual(len(reviewed.eps_share_basis.applied_splits), 1)
        self.assertEqual(versions[1].value.value, Decimal("6.0000000000"))


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


class AsOfSourceListTests(unittest.TestCase):
    """ADP equity on 30.06.2017 as known on 01.09.2019 (real case, 30.10.2026).

    The version review pinned the 10-Q fact with its statement position. The same
    fact is also a stored variant, so it must be listed once, with its position.
    """

    def test_reviewed_source_replaces_the_same_stored_fact(self):
        """The fact appears once among the variants and keeps its statement position."""
        from app.metric_history import periods_as_of

        period = {
            "source_cik": "0000008670",
            "period_start": date(2016, 7, 1),
            "period_end": date(2017, 6, 30),
        }
        annual_report = reported_fact(
            **period, filed=date(2017, 8, 4), accession="0000008670-17-000010"
        )
        group_equity = reported_fact(
            source_cik="0000008670",
            metric="equity",
            tag="StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            value=Decimal("4984100000.0000000000"),
            period_start=date(2017, 6, 30),
            period_end=date(2017, 6, 30),
            filed=date(2019, 5, 3),
            accession="0000008670-19-000013",
            form="10-Q",
            period_kind="instant",
        )
        reviewed_source = FilingValue.model_validate(
            {
                **group_equity,
                "value": Decimal("4984100000"),
                "statement_label": "us-gaap:StockholdersEquityIncludingPortion"
                "AttributableToNoncontrollingInterest",
                "statement_column": "2017-06-30; consolidated instant context",
                "statement_url": "https://www.sec.gov/Archives/edgar/data/8670/"
                "000000867019000013/adp-20190331.xml",
            }
        )
        stored = []
        for metric in ("revenue", "net_income", "eps_diluted", "operating_cash_flow"):
            details = AnnualMetric(status="needs_review", variants=[])
            stored.append((metric, date(2017, 8, 4), details))
        equity = AnnualMetric(
            status="ready",
            value=Decimal("4984100000"),
            variants=[],
            selected_sources=[reviewed_source],
        )
        stored.append(("equity", date(2019, 5, 3), equity))
        stored_versions = [
            {
                "period_start": period["period_start"],
                "period_end": period["period_end"],
                "metric": metric,
                "known_from": known_from,
                "details": details.model_dump(mode="json", exclude={"variants"}),
            }
            for metric, known_from, details in stored
        ]

        periods = periods_as_of(
            [annual_report, group_equity],
            stored_versions,
            date(2019, 9, 1),
            period_rules={"periods": {}, "aliases": {}},
        )

        variants = periods[0].metrics["equity"].variants
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0], reviewed_source)

    def test_latest_view_lists_a_statement_source_once(self):
        """The same rule serves the latest view (59 equity cells listed a fact twice).

        A statement position of a stored fact replaces it; a position that is no
        stored fact, such as noncontrolling interests read from the table, is added.
        """
        from app.fact_identity import add_selected_sources

        stored = FilingValue.model_validate(
            reported_fact(metric="equity", value=Decimal("67678000000.0000000000"))
        )
        same_with_position = stored.model_copy(
            update={
                "value": Decimal("67678000000"),
                "statement_label": "Beginning Balance at Nov. 03, 2024",
                "statement_column": "Total",
            }
        )
        table_only = stored.model_copy(
            update={
                "tag": "MinorityInterest",
                "value": Decimal("12000000"),
                "statement_label": "Noncontrolling interests",
                "statement_column": "Total",
            }
        )
        variants = [stored]

        add_selected_sources(variants, [same_with_position, table_only])

        self.assertEqual(variants, [same_with_position, table_only])


if __name__ == "__main__":
    unittest.main()
