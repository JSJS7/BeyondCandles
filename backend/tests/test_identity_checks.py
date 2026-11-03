import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_pipeline.identity_checks import (
    check_balance,
    check_cash,
    check_eps,
    collect_facts,
    filing_value,
    separate_reviewed_flags,
)

ACCESSION = "0000000000-26-000001"
START = "2025-01-01"
END = "2025-12-31"


def duration(value, accession=ACCESSION, start=START, end=END):
    return {"val": value, "accn": accession, "start": start, "end": end}


def balance(value, accession=ACCESSION, end=END):
    return {"val": value, "accn": accession, "end": end}


class FilingValueTests(unittest.TestCase):
    def test_only_the_named_filing_and_period_are_read(self):
        """Mixing reports would compare restated numbers with original ones."""
        facts = {
            "Assets": [
                balance(100, accession="another"),
                balance(200, end="2024-12-31"),
                balance(300),
            ]
        }

        value = filing_value(facts, ("Assets",), ACCESSION, None, END)

        self.assertEqual(value, Decimal(300))

    def test_duration_and_balance_facts_are_kept_apart(self):
        """A flow reported for the year is not a balance at its last day."""
        facts = {"Liabilities": [duration(100)]}

        self.assertIsNone(filing_value(facts, ("Liabilities",), ACCESSION, None, END))

    def test_collect_keeps_all_units_of_all_issuers(self):
        """A predecessor's facts count too; tags outside the checks are dropped."""
        first = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [balance(1)]}}}}}
        second = {
            "facts": {
                "us-gaap": {
                    "Assets": {"units": {"USD": [balance(2)]}},
                    "Goodwill": {"units": {"USD": [balance(3)]}},
                }
            }
        }

        facts = collect_facts([first, second])

        self.assertEqual(len(facts["Assets"]), 2)
        self.assertNotIn("Goodwill", facts)


class EpsTests(unittest.TestCase):
    def facts(self, shares=1000000000):
        return {"WeightedAverageNumberOfDilutedSharesOutstanding": [duration(shares)]}

    def check(self, eps, income, facts):
        return check_eps(Decimal(eps), Decimal(income), facts, ACCESSION, START, END)

    def test_eps_times_shares_equal_to_income_passes(self):
        """5.00 per share on 1 billion shares is 5 billion of income."""
        self.assertEqual(self.check("5.00", "5000000000", self.facts()), 0)

    def test_eps_of_another_row_is_flagged(self):
        """A value taken from a wrong row misses income by far more than the tolerance."""
        deviation = self.check("2.50", "5000000000", self.facts())

        self.assertEqual(deviation, Decimal("50.0"))

    def test_preferred_dividends_are_taken_from_income(self):
        """Common shareholders receive income after preferred dividends."""
        facts = self.facts()
        facts["PreferredStockDividendsIncomeStatementImpact"] = [duration(1000000000)]

        self.assertEqual(self.check("4.00", "5000000000", facts), 0)

    def test_share_count_tagged_in_thousands_still_passes(self):
        """ConocoPhillips and McDonald's tag the count at the statement's scale."""
        self.assertEqual(self.check("5.00", "5000000000", self.facts(1000000)), 0)

    def test_filing_without_share_count_is_not_available(self):
        """Not checkable must stay distinct from passed."""
        self.assertIsNone(self.check("5.00", "5000000000", {}))


class CashTests(unittest.TestCase):
    def facts(self, **changes):
        facts = {
            "NetCashProvidedByUsedInInvestingActivities": [duration(-600000000)],
            "NetCashProvidedByUsedInFinancingActivities": [duration(-300000000)],
            "CashAndCashEquivalentsPeriodIncreaseDecrease": [duration(100000000)],
        }
        facts.update(changes)
        return facts

    def check(self, operating, facts):
        return check_cash(Decimal(operating), facts, ACCESSION, START, END)

    def test_statement_that_adds_up_passes(self):
        """1,000 - 600 - 300 = 100."""
        self.assertEqual(self.check("1000000000", self.facts()), 0)

    def test_wrong_operating_cash_flow_is_flagged(self):
        """500 served where 1,000 is right: the residual of 500 is 100% of the served value."""
        self.assertEqual(self.check("500000000", self.facts()), Decimal("100.0"))

    def test_exchange_effect_closes_the_sum(self):
        """Foreign currency changes cash without any activity."""
        facts = self.facts(
            EffectOfExchangeRateOnCashAndCashEquivalents=[duration(-200000000)]
        )

        self.assertEqual(self.check("1200000000", facts), 0)

    def test_served_total_already_contains_discontinued_operating_cash(self):
        """AT&T serves a total: adding its discontinued operating cash would count it twice."""
        facts = self.facts(
            CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations=[
                duration(-300000000)
            ],
            CashProvidedByUsedInInvestingActivitiesDiscontinuedOperations=[
                duration(200000000)
            ],
        )

        self.assertEqual(self.check("800000000", facts), 0)

    def test_filing_without_investing_row_is_not_available(self):
        """Not checkable must stay distinct from passed."""
        facts = self.facts()
        del facts["NetCashProvidedByUsedInInvestingActivities"]

        self.assertIsNone(self.check("1000000000", facts))


class BalanceTests(unittest.TestCase):
    def facts(self, **changes):
        facts = {
            "Assets": [balance(10000000000)],
            "Liabilities": [balance(6000000000)],
        }
        facts.update(changes)
        return facts

    def check(self, equity, facts):
        return check_balance(Decimal(equity), facts, ACCESSION, END)

    def test_equal_sides_pass(self):
        """10,000 - 6,000 = 4,000."""
        self.assertEqual(self.check("4000000000", self.facts()), 0)

    def test_liabilities_served_as_equity_are_flagged(self):
        """The wrong-row error this check exists for."""
        deviation = self.check("6000000000", self.facts())

        self.assertEqual(deviation, Decimal("33.3"))

    def test_noncontrolling_and_temporary_equity_close_the_sum(self):
        """Equity served is the parent's: other owners take the rest of the net assets."""
        facts = self.facts(
            MinorityInterest=[balance(500000000)],
            RedeemableNoncontrollingInterestEquityCarryingAmount=[balance(500000000)],
        )

        self.assertEqual(self.check("3000000000", facts), 0)

    def test_one_amount_under_two_tags_counts_once(self):
        """KKR repeats its redeemable interests under two tags."""
        facts = self.facts(
            RedeemableNoncontrollingInterestEquityCarryingAmount=[balance(500000000)],
            TemporaryEquityCarryingAmountAttributableToParent=[balance(500000000)],
        )

        self.assertEqual(self.check("3500000000", facts), 0)

    def test_any_filing_with_both_totals_may_confirm(self):
        """A value from a later equity statement is checked on an earlier balance sheet."""
        facts = {
            "Assets": [balance(10000000000, accession="earlier")],
            "Liabilities": [balance(6000000000, accession="earlier")],
        }

        self.assertEqual(self.check("4000000000", facts), 0)

    def test_no_filing_with_both_totals_is_not_available(self):
        """Many companies do not tag total liabilities at all."""
        self.assertIsNone(self.check("4000000000", {"Assets": [balance(1)]}))


class ReviewTests(unittest.TestCase):
    def flag(self, year=2020, deviation="5.0"):
        return {
            "ticker": "TDG",
            "check": "eps_shares",
            "year": year,
            "deviation_percent": deviation,
        }

    def review(self):
        return {
            "ticker": "TDG",
            "check": "eps_shares",
            "from_year": 2017,
            "to_year": 2021,
            "max_deviation_percent": "10.0",
        }

    def test_flag_within_a_review_is_explained(self):
        """A reviewed structure within its years and size needs nothing more."""
        self.assertEqual(
            separate_reviewed_flags([self.flag()], [self.review()]), ([], [])
        )

    def test_larger_deviation_flags_again(self):
        """The explanation covered a size: a new error on top of it must surface."""
        flag = self.flag(deviation="10.1")
        review = self.review()

        unreviewed, unused = separate_reviewed_flags([flag], [review])

        self.assertEqual(unreviewed, [flag])
        self.assertEqual(unused, [review])

    def test_year_outside_the_review_flags_again(self):
        """Another year of the company has not been read."""
        flag = self.flag(year=2022)

        unreviewed, _ = separate_reviewed_flags([flag], [self.review()])

        self.assertEqual(unreviewed, [flag])

    def test_review_nothing_needs_is_reported(self):
        """A review that explains nothing means the data changed."""
        review = self.review()

        self.assertEqual(separate_reviewed_flags([], [review]), ([], [review]))


if __name__ == "__main__":
    unittest.main()
