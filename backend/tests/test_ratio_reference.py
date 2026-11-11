import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.data_pipeline.ratio_reference import (
    RATIO_NAMES,
    case_differences,
    load_reference,
    ratio_matches,
    separate_known_differences,
)
from app.ratios import PeriodValues, calculate_ratios

# Growth comes from the report columns in the database, not from calculate_ratios, so
# only `check-ratios` compares it.
GROWTH_RATIOS = ("revenue_growth", "eps_growth")
MILLION = Decimal(1_000_000)


def amount(inputs, name):
    """A reference amount in USD; the file states amounts and shares in millions."""
    text = inputs.get(name, {"value": None})["value"]
    if text is None:
        return None
    return Decimal(text) * MILLION


def per_share(inputs, name):
    text = inputs[name]["value"]
    if text is None:
        return None
    return Decimal(text)


def reference_values(inputs):
    return PeriodValues(
        revenue=amount(inputs, "revenue"),
        net_income=amount(inputs, "net_income"),
        eps=per_share(inputs, "eps"),
        equity=amount(inputs, "equity"),
        operating_cash_flow=amount(inputs, "operating_cash_flow"),
        ocf_scope="total",
        shares=amount(inputs, "shares"),
        assets=amount(inputs, "assets"),
        capex=amount(inputs, "capex"),
        operating_income=amount(inputs, "operating_income"),
        preferred_value=amount(inputs, "preferred_value"),
        preferred_dividends=amount(inputs, "preferred_dividends"),
    )


def screener_period(period_end, **ratios):
    """A stand-in for the screener's period: the attributes the comparison reads."""
    values = {name: None for name in RATIO_NAMES}
    values.update(ratios)
    return SimpleNamespace(period_end=period_end, ratios=SimpleNamespace(**values))


class ReferenceFileTests(unittest.TestCase):
    def test_every_case_states_every_input_with_a_source(self):
        """K16: each number of the reference must say where in the report it is."""
        document = load_reference()

        for case in document["cases"]:
            for group in ("current", "previous"):
                for name, entry in case[group].items():
                    with self.subTest(ticker=case["ticker"], group=group, name=name):
                        self.assertTrue(entry["source"])
            self.assertEqual(set(case["expected"]), set(RATIO_NAMES))

    def test_ratio_formulas_reproduce_the_hand_calculated_reference(self):
        """The ratio formulas on real report numbers give the ratios calculated by
        hand from the same numbers. The database and the selection rules play no
        part here; `check-ratios` covers them on the stored data."""
        document = load_reference()
        tolerance = Decimal(document["tolerance"])

        for case in document["cases"]:
            price_entry = case["price"]
            price = Decimal(price_entry["close"]) * Decimal(price_entry["split_factor"])
            values = reference_values(case["current"])
            previous = reference_values(case["previous"])

            ratios = calculate_ratios(values, previous, price)

            for name in RATIO_NAMES:
                if name in GROWTH_RATIOS:
                    continue
                expected_text = case["expected"][name]
                expected = None if expected_text is None else Decimal(expected_text)
                with self.subTest(ticker=case["ticker"], ratio=name):
                    self.assertTrue(
                        ratio_matches(expected, ratios[name], tolerance),
                        f"expected {expected}, calculated {ratios[name]}",
                    )

    def test_known_differences_name_a_case_and_a_finding(self):
        """A known difference must point at a reference ratio and a finding."""
        document = load_reference()
        cases = {(case["ticker"], case["valuation_date"]) for case in document["cases"]}

        for entry in document["known_differences"]:
            with self.subTest(entry=entry):
                self.assertIn((entry["ticker"], entry["valuation_date"]), cases)
                self.assertIn(entry["ratio"], RATIO_NAMES)
                self.assertTrue(entry["finding"])


class ComparisonTests(unittest.TestCase):
    def test_ratio_within_tolerance_matches_and_empty_matches_only_empty(self):
        """Relative tolerance; an empty ratio never matches a number, nor zero."""
        tolerance = Decimal("0.002")

        self.assertTrue(ratio_matches(Decimal("10"), Decimal("10.019"), tolerance))
        self.assertFalse(ratio_matches(Decimal("10"), Decimal("10.021"), tolerance))
        self.assertTrue(ratio_matches(Decimal("-0.5"), Decimal("-0.5009"), tolerance))
        self.assertTrue(ratio_matches(None, None, tolerance))
        self.assertFalse(ratio_matches(None, Decimal("0"), tolerance))
        self.assertFalse(ratio_matches(Decimal("1.5"), None, tolerance))

    def test_case_reports_each_differing_ratio(self):
        """A wrong value and a value where the reference is empty are both reported;
        matching ratios are not."""
        case = {
            "ticker": "TEST",
            "valuation_date": "2025-03-31",
            "period_end": "2024-12-31",
            "expected": {name: None for name in RATIO_NAMES} | {"pe": "12", "ps": "3"},
        }
        latest = screener_period(
            date(2024, 12, 31), pe=Decimal("12"), ps=Decimal("3.5"), pb=Decimal("2")
        )

        differences = case_differences(case, latest, Decimal("0.002"))

        self.assertEqual([entry["ratio"] for entry in differences], ["pb", "ps"])

    def test_case_with_another_period_is_one_difference(self):
        """When the screener values another year, comparing its ratios means nothing."""
        case = {
            "ticker": "TEST",
            "valuation_date": "2025-03-31",
            "period_end": "2024-12-31",
            "expected": {name: None for name in RATIO_NAMES},
        }
        latest = screener_period(date(2023, 12, 31))

        differences = case_differences(case, latest, Decimal("0.002"))

        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]["ratio"], "period_end")

    def test_known_difference_is_explained_and_a_fixed_one_is_unused(self):
        """A difference named in the file is explained; a named one that no longer
        occurs is reported, so that the file is updated after the fix."""
        differences = [
            {"ticker": "A", "valuation_date": "2025-03-31", "ratio": "pe"},
            {"ticker": "B", "valuation_date": "2025-03-31", "ratio": "ps"},
        ]
        known = [
            {
                "ticker": "A",
                "valuation_date": "2025-03-31",
                "ratio": "pe",
                "finding": "N1",
            },
            {
                "ticker": "C",
                "valuation_date": "2025-03-31",
                "ratio": "pb",
                "finding": "N2",
            },
        ]

        unexplained, explained, unused = separate_known_differences(differences, known)

        self.assertEqual([entry["ticker"] for entry in unexplained], ["B"])
        self.assertEqual(explained[0]["finding"], "N1")
        self.assertEqual([entry["ticker"] for entry in unused], ["C"])


if __name__ == "__main__":
    unittest.main()
