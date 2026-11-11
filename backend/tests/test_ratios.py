import subprocess
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.ratios import (
    PeriodValues,
    calculate_ratios,
    common_equity,
    exchangeable_unit_periods,
    participating_preferred_tickers,
    ps_excludes_exchangeable_units,
    shares_for_ratios,
)

BACKEND = Path(__file__).resolve().parents[1]


def period(**values):
    """A healthy company in USD; each test changes only what it examines."""
    defaults = {
        "revenue": Decimal(1000),
        "net_income": Decimal(100),
        "eps": Decimal(2),
        "equity": Decimal(500),
        "operating_cash_flow": Decimal(150),
        "ocf_scope": "total",
        "shares": Decimal(50),
        "assets": Decimal(2000),
        "capex": Decimal(40),
        "operating_income": Decimal(130),
        "preferred_value": None,
        "preferred_dividends": None,
    }
    return PeriodValues(**{**defaults, **values})


class ImportTests(unittest.TestCase):
    def test_calculation_modules_load_without_database_settings(self):
        """Regression N57: offline commands must not need database settings to import."""
        code = (
            "import sys; import app.ratios, app.annual_financials, "
            "app.automatic_metrics, app.data_pipeline.sec; "
            "sys.exit(1 if 'app.database' in sys.modules else 0)"
        )

        result = subprocess.run(
            [sys.executable, "-c", code], cwd=BACKEND, capture_output=True
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())


class SharesForRatiosTests(unittest.TestCase):
    def test_reported_count_is_preferred(self):
        """A tagged count is used as it stands, even when net income / EPS differs."""
        count, basis = shares_for_ratios(period(shares=Decimal(48)))

        self.assertEqual(count, Decimal(48))
        self.assertEqual(basis, "reported")

    def test_visa_count_is_calculated_from_its_report_figures(self):
        """Visa FY2024 has no tag: 19,743 / 9.73 matches 2,029 mln in the 10-K's table."""
        values = period(shares=None, net_income=Decimal("19743e6"), eps=Decimal("9.73"))

        count, basis = shares_for_ratios(values)

        self.assertEqual(basis, "calculated")
        self.assertAlmostEqual(float(count) / 1e6, 2029, delta=1)

    def test_calculation_subtracts_preferred_dividends(self):
        """EPS is after preferred dividends: (100 - 10) / 2 = 45 shares."""
        values = period(shares=None, preferred_dividends=Decimal(10))

        count, _ = shares_for_ratios(values)

        self.assertEqual(count, Decimal(45))

    def test_loss_gives_a_positive_count(self):
        """Loss divided by loss per share is still a share count (BRK-B 2022)."""
        values = period(shares=None, net_income=Decimal(-100), eps=Decimal(-2))

        count, _ = shares_for_ratios(values)

        self.assertEqual(count, Decimal(50))

    def test_no_count_without_a_usable_calculation(self):
        """Zero EPS, a missing input or opposite signs give no count, never a guess."""
        for values in (
            period(shares=None, eps=Decimal(0)),
            period(shares=None, eps=None),
            period(shares=None, net_income=None),
            period(shares=None, net_income=Decimal(-100)),
        ):
            with self.subTest(values=values):
                self.assertEqual(shares_for_ratios(values), (None, None))


class CommonEquityTests(unittest.TestCase):
    def test_positive_preferred_value_is_deducted(self):
        """Common equity is equity less the preferred stock inside it."""
        values = period(preferred_value=Decimal(100))

        self.assertEqual(common_equity(values), Decimal(400))

    def test_participating_preferred_is_not_deducted(self):
        """Visa counts it as converted in the shares, so deducting would count it twice."""
        values = period(preferred_value=Decimal(100))

        self.assertEqual(common_equity(values, participating=True), Decimal(500))

    def test_unknown_preferred_amount_leaves_common_equity_empty(self):
        """Material dividends without a value: the amount to deduct is unknown (M27)."""
        values = period(preferred_dividends=Decimal(20))

        self.assertIsNone(common_equity(values))

    def test_visa_is_the_recorded_participating_company(self):
        """The rule file names Visa; a report must back every entry."""
        self.assertEqual(participating_preferred_tickers(), {"V"})


class ExchangeableUnitTests(unittest.TestCase):
    def test_recorded_companies_and_their_affected_periods(self):
        """M42: Blackstone still has Holdings units; KKR exchanged its units in 2022.
        N115: Charter's diluted count includes the Advance/Newhouse units in 2016 and
        2017 and leaves them out from 2018, so only periods from 2018 are affected."""
        self.assertEqual(
            exchangeable_unit_periods(),
            {
                "BX": (None, None),
                "KKR": (None, date(2021, 12, 31)),
                "CHTR": (date(2018, 12, 31), None),
            },
        )

    def test_period_is_marked_up_to_and_including_the_last_listed_period(self):
        """The last listed period is affected; the next one and other tickers are not."""
        unit_periods = {"OPEN": (None, None), "CLOSED": (None, date(2021, 12, 31))}

        self.assertTrue(
            ps_excludes_exchangeable_units("OPEN", date(2025, 12, 31), unit_periods)
        )
        self.assertTrue(
            ps_excludes_exchangeable_units("CLOSED", date(2021, 12, 31), unit_periods)
        )
        self.assertFalse(
            ps_excludes_exchangeable_units("CLOSED", date(2022, 12, 31), unit_periods)
        )
        self.assertFalse(
            ps_excludes_exchangeable_units("OTHER", date(2021, 12, 31), unit_periods)
        )

    def test_period_is_marked_from_and_including_the_first_listed_period(self):
        """A period before the first listed one is not affected (its diluted count
        already includes the units); the first listed period and later ones are."""
        unit_periods = {"LATE": (date(2018, 12, 31), None)}

        self.assertFalse(
            ps_excludes_exchangeable_units("LATE", date(2017, 12, 31), unit_periods)
        )
        self.assertTrue(
            ps_excludes_exchangeable_units("LATE", date(2018, 12, 31), unit_periods)
        )
        self.assertTrue(
            ps_excludes_exchangeable_units("LATE", date(2025, 12, 31), unit_periods)
        )


class RatioTests(unittest.TestCase):
    def test_known_results(self):
        """Hand-calculated results for one company with an earlier year."""
        previous = period(equity=Decimal(300), assets=Decimal(1000))

        ratios = calculate_ratios(period(), previous, price=Decimal(40))

        # Price 40, EPS 2, 50 shares, so market value 2000.
        self.assertEqual(ratios["pe"], Decimal(20))
        self.assertEqual(ratios["pb"], Decimal(4))  # 2000 / 500
        self.assertEqual(ratios["ps"], Decimal(2))  # 2000 / 1000
        self.assertEqual(ratios["roe"], Decimal("0.25"))  # 100 / ((500 + 300) / 2)
        # 100 / ((2000 + 1000) / 2)
        self.assertAlmostEqual(float(ratios["roa"]), 100 / 1500)
        self.assertEqual(ratios["net_margin"], Decimal("0.1"))
        self.assertEqual(ratios["operating_margin"], Decimal("0.13"))
        self.assertEqual(ratios["ocf_to_net_income"], Decimal("1.5"))
        self.assertEqual(ratios["fcf_margin"], Decimal("0.11"))  # (150 - 40) / 1000
        self.assertEqual(ratios["equity_to_assets"], Decimal("0.25"))

    def test_preferred_stock_changes_book_value_and_return(self):
        """P/B and ROE are for common holders: 2000 / 400 and 90 / ((400 + 200) / 2)."""
        current = period(preferred_value=Decimal(100), preferred_dividends=Decimal(10))
        previous = period(equity=Decimal(300), preferred_value=Decimal(100))

        ratios = calculate_ratios(current, previous, price=Decimal(40))

        self.assertEqual(ratios["pb"], Decimal(5))
        self.assertEqual(ratios["roe"], Decimal("0.3"))

    def test_redemption_gain_stays_in_income_to_common(self):
        """Negative "dividends" raise income as they do in EPS (GM 2024): 150 / 500."""
        values = period(preferred_dividends=Decimal(-50))

        ratios = calculate_ratios(values, period(), price=Decimal(40))

        self.assertEqual(ratios["roe"], Decimal("0.3"))

    def test_loss_leaves_earnings_ratios_empty(self):
        """P/E and OCF to net income have no meaning under a loss; margins stay negative."""
        values = period(net_income=Decimal(-100), eps=Decimal(-2))

        ratios = calculate_ratios(values, None, price=Decimal(40))

        self.assertIsNone(ratios["pe"])
        self.assertIsNone(ratios["ocf_to_net_income"])
        self.assertEqual(ratios["net_margin"], Decimal("-0.1"))

    def test_negative_equity_leaves_book_ratios_empty(self):
        """Negative common equity gives no P/B or ROE, in either of the two years."""
        negative = period(equity=Decimal(-50))

        current_year = calculate_ratios(negative, period(), price=Decimal(40))
        earlier_year = calculate_ratios(period(), negative, price=Decimal(40))

        self.assertIsNone(current_year["pb"])
        self.assertIsNone(current_year["roe"])
        self.assertEqual(current_year["equity_to_assets"], Decimal("-0.025"))
        self.assertIsNone(earlier_year["roe"])

    def test_without_an_earlier_year_average_ratios_are_empty(self):
        """ROE and ROA use averages, so the first known year has none."""
        ratios = calculate_ratios(period(), None, price=Decimal(40))

        self.assertIsNone(ratios["roe"])
        self.assertIsNone(ratios["roa"])
        self.assertEqual(ratios["net_margin"], Decimal("0.1"))

    def test_without_a_price_only_price_ratios_are_empty(self):
        """JCI before the merger has no comparable price; other ratios stay."""
        ratios = calculate_ratios(period(), period(), price=None)

        for name in ("pe", "pb", "ps"):
            self.assertIsNone(ratios[name])
        self.assertEqual(ratios["net_margin"], Decimal("0.1"))

    def test_continuing_cash_flow_is_not_compared_with_total_income(self):
        """Continuing OCF against total income mixes scopes; FCF margin stays, with revenue."""
        ratios = calculate_ratios(period(ocf_scope="continuing"), None, Decimal(40))

        self.assertIsNone(ratios["ocf_to_net_income"])
        self.assertEqual(ratios["fcf_margin"], Decimal("0.11"))

    def test_missing_inputs_leave_only_their_ratios_empty(self):
        """A bank without capex or operating income keeps all its other ratios."""
        ratios = calculate_ratios(
            period(capex=None, operating_income=None), None, Decimal(40)
        )

        self.assertIsNone(ratios["fcf_margin"])
        self.assertIsNone(ratios["operating_margin"])
        self.assertEqual(ratios["pe"], Decimal(20))

    def test_non_positive_revenue_leaves_revenue_ratios_empty(self):
        """Revenue of zero or below gives no margin and no P/S."""
        ratios = calculate_ratios(period(revenue=Decimal(0)), None, Decimal(40))

        for name in ("ps", "net_margin", "operating_margin", "fcf_margin"):
            self.assertIsNone(ratios[name])

    def test_calculated_shares_feed_price_ratios(self):
        """Without a tag P/B and P/S use the calculated count: 40 * 50 / 500."""
        ratios = calculate_ratios(period(shares=None), None, Decimal(40))

        self.assertEqual(ratios["pb"], Decimal(4))


if __name__ == "__main__":
    unittest.main()
