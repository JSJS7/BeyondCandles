"""Automatic measures: which report gives a cell its value, and from which day."""

import unittest
from datetime import date
from decimal import Decimal

from app.automatic_metrics import (
    automatic_versions,
    largest_tolerated_difference,
    preferred_deduction,
)

PERIOD = (date(2022, 1, 1), date(2022, 12, 31))
NO_ALIASES = {"aliases": {}}
NO_SPLITS = {"basis_date": "2026-10-13", "actions": []}


def fact(metric, tag, value, filed=date(2023, 2, 15), accession="first", **changes):
    is_balance = metric in ("assets", "preferred_value")
    row = {
        "source_cik": "0000000001",
        "metric": metric,
        "taxonomy": "us-gaap",
        "tag": tag,
        "unit": "shares" if metric == "shares_diluted" else "USD",
        "value": Decimal(value),
        "period_start": PERIOD[1] if is_balance else PERIOD[0],
        "period_end": PERIOD[1],
        "filed": filed,
        "accession": accession,
        "form": "10-K",
        "period_kind": "instant" if is_balance else "annual",
        "history_eligible": True,
    }
    row.update(changes)
    return row


def versions_of(facts, metric, policy=NO_SPLITS, periods=(PERIOD,)):
    versions = automatic_versions("TEST", facts, periods, policy, NO_ALIASES)
    selected = [version for version in versions if version.metric == metric]
    return sorted(selected, key=lambda version: version.known_from)


def scale_correction(reported_value="732.3", multiplier=1000000):
    return {
        "ticker": "TEST",
        "accession": "first",
        "period_end": str(PERIOD[1]),
        "tag": "WeightedAverageNumberOfDilutedSharesOutstanding",
        "reported_value": reported_value,
        "multiplier": multiplier,
    }


def preferred_value(facts):
    versions = versions_of(facts, "preferred_value")
    return versions[-1].value if versions else None


class VersionTests(unittest.TestCase):
    def test_repeated_value_keeps_its_first_report_date(self):
        """A value is known from its first report; repeating it adds no version."""
        facts = [
            fact("assets", "Assets", "100"),
            fact("assets", "Assets", "100", date(2024, 2, 15), "second"),
        ]

        versions = versions_of(facts, "assets")

        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].known_from, date(2023, 2, 15))
        self.assertEqual(versions[0].confirmation, "automatic")

    def test_restated_value_starts_a_new_version(self):
        """A backtest before the restatement must still see the first number."""
        facts = [
            fact("capex", "PaymentsToAcquirePropertyPlantAndEquipment", "100"),
            fact(
                "capex",
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "95",
                date(2024, 2, 15),
                "second",
            ),
        ]

        versions = versions_of(facts, "capex")

        self.assertEqual(
            [(version.known_from, version.value.value) for version in versions],
            [(date(2023, 2, 15), Decimal("100")), (date(2024, 2, 15), Decimal("95"))],
        )

    def test_backup_tag_counts_only_while_the_main_one_is_unknown(self):
        """MCK has both capex tags, and the backup is wider: 859 against 537 million."""
        main = "PaymentsToAcquirePropertyPlantAndEquipment"
        backup = "PaymentsToAcquireProductiveAssets"

        both = versions_of(
            [fact("capex", backup, "859"), fact("capex", main, "537")], "capex"
        )
        only_backup = versions_of([fact("capex", backup, "859")], "capex")

        self.assertEqual(both[-1].value.value, Decimal("537"))
        self.assertEqual(only_backup[-1].value.selected_tag, backup)

    def test_facts_never_create_a_period(self):
        """JCI has capex for a period no reviewed metric has; it stays out."""
        facts = [
            fact(
                "capex",
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "100",
                period_start=date(2021, 1, 1),
                period_end=date(2021, 12, 31),
            )
        ]

        self.assertEqual(versions_of(facts, "capex"), [])

    def test_balance_joins_its_period_by_end_date(self):
        """A balance has no start date, so the period end alone identifies it."""
        versions = versions_of([fact("assets", "Assets", "100")], "assets")

        self.assertEqual((versions[0].period_start, versions[0].period_end), PERIOD)

    def test_ineligible_facts_and_other_period_kinds_are_left_out(self):
        """Facts outside the approved history and year-to-date values give no cell."""
        tag = "PaymentsToAcquirePropertyPlantAndEquipment"
        facts = [
            fact("capex", tag, "100", history_eligible=False),
            fact("capex", tag, "25", accession="ytd", period_kind="ytd"),
        ]

        self.assertEqual(versions_of(facts, "capex"), [])


class ShareCountTests(unittest.TestCase):
    TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"
    SPLIT = {
        "basis_date": "2026-10-13",
        "actions": [
            {
                "ticker": "TEST",
                "date": "2023-06-01",
                "new_shares": 2,
                "old_shares": 1,
                "source_url": "https://example.com/split",
            }
        ],
    }

    def test_split_after_the_filing_is_applied_and_keeps_the_original(self):
        """A 2:1 split doubles an earlier count; EPS does the opposite."""
        versions = versions_of(
            [fact("shares_diluted", self.TAG, "100")], "shares_diluted", self.SPLIT
        )

        cell = versions[0].value
        self.assertEqual(cell.value, Decimal("200"))
        self.assertEqual(cell.eps_share_basis.reported_value, Decimal("100"))
        self.assertEqual(cell.calculation, "Reported shares × 2 / 1")

    def test_restating_a_split_does_not_create_a_version(self):
        """The later report shows 200 already split, which is the same number."""
        facts = [
            fact("shares_diluted", self.TAG, "100"),
            fact("shares_diluted", self.TAG, "200", date(2024, 2, 15), "second"),
        ]

        versions = versions_of(facts, "shares_diluted", self.SPLIT)

        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].known_from, date(2023, 2, 15))

    def test_scale_error_gives_way_to_a_correct_report(self):
        """MCD reports 732.3 million but later tags it as 732.3 shares; COR the reverse."""
        tagged_wrong_later = versions_of(
            [
                fact("shares_diluted", self.TAG, "732300000"),
                fact("shares_diluted", self.TAG, "732.3", date(2024, 2, 15), "second"),
            ],
            "shares_diluted",
        )
        tagged_wrong_first = versions_of(
            [
                fact("shares_diluted", self.TAG, "218000"),
                fact(
                    "shares_diluted",
                    self.TAG,
                    "217786000",
                    date(2024, 2, 15),
                    "second",
                ),
            ],
            "shares_diluted",
        )

        self.assertEqual(
            [version.value.value for version in tagged_wrong_later],
            [Decimal("732300000")],
        )
        self.assertEqual(tagged_wrong_first[0].known_from, date(2024, 2, 15))

    def test_scale_check_sees_other_years_and_leaves_the_cell_empty(self):
        """COP tags 2018 as 1.2 million in every report; 1078 million in 2020 reveals it."""
        later_year = {
            "period_start": date(2020, 1, 1),
            "period_end": date(2020, 12, 31),
            "filed": date(2021, 2, 15),
            "accession": "later",
        }
        versions = versions_of(
            [
                fact("shares_diluted", self.TAG, "1176000"),
                fact("shares_diluted", self.TAG, "1078000000", **later_year),
            ],
            "shares_diluted",
        )

        self.assertEqual(versions, [])

    def test_large_split_is_not_taken_for_a_scale_error(self):
        """A 200:1 split leaves the old count under 1%, unless it is normalized first."""
        policy = {
            "basis_date": "2026-10-13",
            "actions": [dict(self.SPLIT["actions"][0], new_shares=200)],
        }
        later_year = {
            "period_start": date(2024, 1, 1),
            "period_end": date(2024, 12, 31),
            "filed": date(2025, 2, 15),
            "accession": "later",
        }
        versions = versions_of(
            [
                fact("shares_diluted", self.TAG, "1000000"),
                fact("shares_diluted", self.TAG, "200000000", **later_year),
            ],
            "shares_diluted",
            policy,
        )

        self.assertEqual(versions[0].value.value, Decimal("200000000"))

    def test_basic_count_is_labelled(self):
        """XOM and AJG report no diluted count; the reader must see the difference."""
        tag = "WeightedAverageNumberOfSharesOutstandingBasic"

        versions = versions_of([fact("shares_diluted", tag, "100")], "shares_diluted")

        self.assertIn("Basic share count", versions[0].value.selection_note)


class ShareScaleCorrectionTests(unittest.TestCase):
    """MCD tags the digits of its table, "732.3", and the table says "in millions"."""

    TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"

    def correct(self, facts, correction):
        versions = automatic_versions(
            "TEST", facts, (PERIOD,), NO_SPLITS, NO_ALIASES, [correction]
        )
        return [version for version in versions if version.metric == "shares_diluted"]

    def test_listed_fact_gets_the_unit_and_keeps_the_tag_value(self):
        """The cell has the report's number; its source still shows what was filed."""
        versions = self.correct(
            [fact("shares_diluted", self.TAG, "732.3")], scale_correction()
        )

        cell = versions[0].value
        self.assertEqual(cell.value, Decimal("732300000"))
        self.assertEqual(cell.selected_sources[0].value, Decimal("732.3"))
        self.assertEqual(cell.calculation, "Tag value × 1000000")
        self.assertEqual(versions[0].known_from, date(2023, 2, 15))

    def test_later_reports_repeating_the_wrong_tag_change_nothing(self):
        """The rule names the first report; repeats are scale errors and are dropped."""
        versions = self.correct(
            [
                fact("shares_diluted", self.TAG, "732.3"),
                fact("shares_diluted", self.TAG, "732.3", date(2024, 2, 15), "second"),
            ],
            scale_correction(),
        )

        self.assertEqual(len(versions), 1)

    def test_changed_source_stops_the_build(self):
        """Multiplying a number nobody checked would be an unnoticed new error."""
        with self.assertRaisesRegex(ValueError, "stale"):
            self.correct([fact("shares_diluted", self.TAG, "700")], scale_correction())

    def test_rule_without_a_fact_stops_the_build(self):
        """A rule that matches nothing means its evidence no longer applies."""
        with self.assertRaisesRegex(ValueError, "matches no fact"):
            self.correct(
                [fact("shares_diluted", self.TAG, "732.3", accession="other")],
                scale_correction(),
            )

    def test_other_companies_ignore_the_rule(self):
        """A rule applies only to its own ticker."""
        correction = dict(scale_correction(), ticker="OTHER")

        versions = self.correct(
            [fact("shares_diluted", self.TAG, "732300000")], correction
        )

        self.assertEqual(versions[0].value.value, Decimal("732300000"))


class PreferredValueTests(unittest.TestCase):
    """Cases checked against the balance sheets of the 10-K reports."""

    DIVIDENDS = "PreferredStockDividendsIncomeStatementImpact"
    VALUE = "PreferredStockValue"

    def test_par_value_gives_way_to_the_liquidation_preference(self):
        """GE 2022 shows 6 million on the balance sheet and a 5795 million preference."""
        cell = preferred_value(
            [
                fact("preferred_dividends", self.DIVIDENDS, "289"),
                fact("preferred_value", self.VALUE, "6"),
                fact(
                    "preferred_value",
                    "PreferredStockLiquidationPreferenceValue",
                    "5795",
                ),
            ]
        )

        self.assertEqual(cell.value, Decimal("5795"))

    def test_zero_on_the_balance_sheet_does_not_hide_a_value(self):
        """COF shows 0 in the par line and 4845 million with additional paid-in capital."""
        cell = preferred_value(
            [
                fact("preferred_dividends", self.DIVIDENDS, "228"),
                fact("preferred_value", self.VALUE, "0"),
                fact(
                    "preferred_value",
                    "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
                    "4845",
                ),
            ]
        )

        self.assertEqual(cell.value, Decimal("4845"))

    def test_only_a_zero_is_kept_as_reported(self):
        """AXP reports zero; whether that is believable is decided with its dividends."""
        cell = preferred_value(
            [
                fact("preferred_dividends", self.DIVIDENDS, "58"),
                fact("preferred_value", self.VALUE, "0"),
            ]
        )

        self.assertEqual(cell.value, Decimal("0"))

    def test_no_value_tag_leaves_the_cell_empty(self):
        """BA and MS have no value tag at all: an empty cell, not an assumed zero."""
        self.assertIsNone(
            preferred_value([fact("preferred_dividends", self.DIVIDENDS, "345")])
        )

    def test_scale_error_is_rejected(self):
        """AVGO 2020 tags a 3.7 billion preference as 4 million, with 297 of dividends."""
        cell = preferred_value(
            [
                fact("preferred_dividends", self.DIVIDENDS, "297"),
                fact("preferred_value", self.VALUE, "0"),
                fact(
                    "preferred_value",
                    "PreferredStockLiquidationPreferenceValue",
                    "4",
                ),
            ]
        )

        self.assertEqual(cell.value, Decimal("0"))

    def test_value_without_dividends_is_accepted(self):
        """V reports a value and no dividend tag; nothing contradicts the value."""
        cell = preferred_value([fact("preferred_value", self.VALUE, "3000")])

        self.assertEqual(cell.value, Decimal("3000"))

    def test_later_dividends_can_reject_a_known_value(self):
        """The test uses what was known on each day, so no knowledge moves backwards."""
        facts = [
            fact("preferred_value", self.VALUE, "6"),
            fact("preferred_value", "PreferredStockLiquidationPreferenceValue", "5795"),
            fact(
                "preferred_dividends",
                self.DIVIDENDS,
                "289",
                date(2024, 2, 15),
                "second",
            ),
        ]

        versions = versions_of(facts, "preferred_value")

        self.assertEqual(
            [(version.known_from, version.value.value) for version in versions],
            [(date(2023, 2, 15), Decimal("6")), (date(2024, 2, 15), Decimal("5795"))],
        )


class OperatingIncomeExclusionTests(unittest.TestCase):
    """SHW tags Segment profit as operating income: 3397 million in 2022."""

    TAG = "OperatingIncomeLoss"

    def versions(self, facts, exclusion):
        return [
            version
            for version in automatic_versions(
                "TEST", facts, (PERIOD,), NO_SPLITS, NO_ALIASES, (), [exclusion]
            )
            if version.metric == "operating_income"
        ]

    def test_listed_company_has_an_empty_cell(self):
        """A sum of segments would overstate the margin, so nothing is shown."""
        versions = self.versions(
            [fact("operating_income", self.TAG, "3397")], {"ticker": "TEST"}
        )

        self.assertEqual(versions, [])

    def test_other_measures_of_the_company_stay(self):
        """Only the operating income cell is affected."""
        kept = automatic_versions(
            "TEST",
            [
                fact("operating_income", self.TAG, "3397"),
                fact("assets", "Assets", "100"),
            ],
            (PERIOD,),
            NO_SPLITS,
            NO_ALIASES,
            (),
            [{"ticker": "TEST"}],
        )

        self.assertEqual([version.metric for version in kept], ["assets"])

    def test_limit_keeps_later_periods(self):
        """PH: a sum of segments through 2017, a company-wide row from 2018."""
        earlier = (date(2021, 1, 1), date(2021, 12, 31))
        facts = [
            fact(
                "operating_income",
                self.TAG,
                "1790",
                date(2022, 2, 15),
                "earlier",
                period_start=earlier[0],
                period_end=earlier[1],
            ),
            fact("operating_income", self.TAG, "2038"),
        ]

        versions = [
            version
            for version in automatic_versions(
                "TEST",
                facts,
                (earlier, PERIOD),
                NO_SPLITS,
                NO_ALIASES,
                (),
                [{"ticker": "TEST", "period_end_to": "2021-12-31"}],
            )
            if version.metric == "operating_income"
        ]

        self.assertEqual([version.period_end for version in versions], [PERIOD[1]])

    def test_other_companies_ignore_the_rule(self):
        """A rule applies only to its own ticker."""
        versions = self.versions(
            [fact("operating_income", self.TAG, "100")], {"ticker": "OTHER"}
        )

        self.assertEqual(len(versions), 1)

    def test_rule_without_facts_stops_the_build(self):
        """A company that stopped using the tag makes the rule meaningless."""
        with self.assertRaisesRegex(ValueError, "matches no fact"):
            self.versions([fact("assets", "Assets", "100")], {"ticker": "TEST"})


class PreferredDeductionTests(unittest.TestCase):
    """Rule M27 on numbers from the reports, in millions."""

    def test_a_plausible_value_is_deducted(self):
        """JPM 2024: the balance sheet shows 20,050."""
        deduction = preferred_deduction(Decimal(20050), Decimal(1259), Decimal(344758))

        self.assertEqual(deduction, Decimal(20050))

    def test_no_preferred_stock_deducts_zero(self):
        """Most companies have neither a value nor dividends."""
        self.assertEqual(preferred_deduction(None, None, Decimal(1000)), 0)
        self.assertEqual(preferred_deduction(Decimal(0), None, Decimal(1000)), 0)

    def test_dividends_without_a_value_leave_the_ratio_empty(self):
        """MS 2024: 590 of dividends and no tag, though the report shows 9,750."""
        self.assertIsNone(preferred_deduction(None, Decimal(590), Decimal(104511)))

    def test_a_reported_zero_does_not_prove_there_is_none(self):
        """PNC 2024 tags 0, while its capital table shows 5,749 with related surplus."""
        self.assertIsNone(preferred_deduction(Decimal(0), Decimal(352), Decimal(54425)))

    def test_immaterial_dividends_deduct_zero(self):
        """OKE pays 1 million on 17,036 of equity: below any threshold of the rule."""
        self.assertEqual(preferred_deduction(None, Decimal(1), Decimal(17036)), 0)

    def test_negative_equity_leaves_the_ratio_empty(self):
        """BA 2024: dividends and negative equity give nothing to compare them with."""
        self.assertIsNone(preferred_deduction(None, Decimal(58), Decimal(-3908)))

    def test_negative_dividends_count_as_none(self):
        """GM tags a redemption gain as negative dividends: 2024, -1,181."""
        self.assertEqual(preferred_deduction(None, Decimal(-1181), Decimal(1000)), 0)


class ToleranceTests(unittest.TestCase):
    """The correction rule of project-principles.md, section 5.1, for new data."""

    def test_revenue_part_decides_for_a_mid_sized_value(self):
        """0.5% of revenue is more than 5% of the value and under the 25% cap."""
        tolerance = largest_tolerated_difference("capex", Decimal(1000), Decimal(20000))

        self.assertEqual(tolerance, Decimal(100))

    def test_cap_stops_a_tolerance_larger_than_a_small_value(self):
        """MS dividends: 612 against revenue of about 60,000 would tolerate half of it."""
        tolerance = largest_tolerated_difference(
            "preferred_dividends", Decimal(612), Decimal(60000)
        )

        self.assertEqual(tolerance, Decimal(153))

    def test_share_of_value_decides_for_a_large_value(self):
        """Assets dwarf revenue, so 5% of the value is the larger number."""
        tolerance = largest_tolerated_difference(
            "assets", Decimal(100000), Decimal(10000)
        )

        self.assertEqual(tolerance, Decimal(5000))

    def test_share_counts_use_five_percent_alone(self):
        """Revenue in USD says nothing about a number of shares."""
        tolerance = largest_tolerated_difference(
            "shares_diluted", Decimal(1000), Decimal(1000000)
        )

        self.assertEqual(tolerance, Decimal(50))


if __name__ == "__main__":
    unittest.main()
