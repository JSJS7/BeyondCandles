"""Ratios of one company for every annual period, valued at the fiscal year's last close."""

import unittest
from datetime import date
from unittest.mock import patch

from test_screener import FIRST_YEAR, SECOND_YEAR, ScreenerDatabaseTestCase

from app.company_ratios import annual_ratio_history


class AnnualRatioHistoryTests(ScreenerDatabaseTestCase):
    def history(self, company_id=1, ticker="TEST"):
        with self.database.connect() as connection:
            history = annual_ratio_history(connection, company_id, ticker)
        return [period.model_dump(mode="json") for period in history]

    def test_every_period_takes_the_last_close_of_its_fiscal_year(self):
        """Both years end on a weekend, so each takes the Friday before; the later close
        of 80 is never used. EPS is 2 in both years: P/E 30 / 2 = 15 and 40 / 2 = 20."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 2, 1), SECOND_YEAR)
        self.add_close(date(2022, 12, 30), close="30")
        self.add_close(date(2023, 12, 29), close="40")
        self.add_close(date(2024, 3, 28), close="80")

        history = self.history()

        self.assertEqual(
            [period["period_end"] for period in history], ["2023-12-31", "2022-12-31"]
        )
        self.assertEqual(history[0]["price_date"], "2023-12-29")
        self.assertEqual(history[0]["ratios"]["pe"], "20")
        self.assertEqual(history[1]["price_date"], "2022-12-30")
        self.assertEqual(history[1]["ratios"]["pe"], "15")

    def test_average_ratios_use_the_year_before(self):
        """ROE divides net income 100 by the average of equity 500 and 500: 0.2. The
        first year has no year before, so its ROE stays empty, as in the screener."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 2, 1), SECOND_YEAR)

        history = self.history()

        self.assertEqual(history[0]["ratios"]["roe"], "0.2")
        self.assertIsNone(history[1]["ratios"]["roe"])

    def test_without_a_close_in_seven_days_only_price_ratios_are_empty(self):
        """A close 11 days before the period end is not the price of that day."""
        self.add_year(date(2024, 2, 1))
        self.add_close(date(2023, 12, 20))

        period = self.history()[0]

        self.assertIsNone(period["price_date"])
        self.assertIsNone(period["ratios"]["pe"])
        self.assertIsNone(period["ratios"]["pb"])
        self.assertEqual(period["ratios"]["net_margin"], "0.1")

    def test_later_restatement_does_not_replace_the_first_annual_report(self):
        """Regression for restated years (IBM 2019 restated without Kyndryl in 2021): the
        year-end price includes the separated business, so revenue restated to 500 a
        year later is not used. Net margin stays 100 / 1000 = 0.1, not 100 / 500."""
        self.add_year(date(2024, 2, 1))
        self.add_version("revenue", "500", date(2025, 2, 1))

        period = self.history()[0]

        self.assertEqual(period["ratios"]["net_margin"], "0.1")

    def test_year_before_is_taken_as_the_later_report_states_it(self):
        """Each row follows its own report. The second report restates the first year's
        equity from 500 to 300: the first row keeps 500 / 2000 = 0.25 equity to assets,
        and the second year's ROE averages 500 and 300: 100 / 400 = 0.25."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 2, 1), SECOND_YEAR)
        self.add_version("equity", "300", date(2024, 2, 1), FIRST_YEAR)

        history = self.history()

        self.assertEqual(history[1]["ratios"]["equity_to_assets"], "0.25")
        self.assertEqual(history[0]["ratios"]["roe"], "0.25")

    def test_value_filed_after_the_first_annual_report_is_not_used(self):
        """Assets first known from a later filing were unknown at the first report, so
        ROA and equity to assets stay empty, as the screener showed them then."""
        for metric, value in (
            ("revenue", "1000"),
            ("net_income", "100"),
            ("eps_diluted", "2"),
            ("equity", "500"),
            ("operating_cash_flow", "150"),
            ("shares_diluted", "50"),
        ):
            self.add_version(metric, value, date(2024, 2, 1))
        self.add_version("assets", "2000", date(2025, 2, 1))

        period = self.history()[0]

        self.assertIsNone(period["ratios"]["equity_to_assets"])
        self.assertEqual(period["ratios"]["net_margin"], "0.1")

    def test_jci_period_before_the_merger_has_no_price_ratios(self):
        """Prices through 02.09.2016 belong to Tyco International, so FY2015 of Johnson
        Controls Inc. gets no price ratios; FY2016 ends after the merger."""
        fiscal_2015 = (date(2014, 10, 1), date(2015, 9, 30))
        fiscal_2016 = (date(2015, 10, 1), date(2016, 9, 30))
        self.add_year(date(2015, 11, 18), fiscal_2015, company=2)
        self.add_year(date(2016, 12, 1), fiscal_2016, company=2)
        self.add_close(date(2015, 9, 30), company=2)
        self.add_close(date(2016, 9, 30), company=2)

        history = self.history(company_id=2, ticker="JCI")

        self.assertEqual(history[0]["ratios"]["pe"], "20")
        self.assertIsNone(history[1]["ratios"]["pe"])
        self.assertEqual(history[1]["ratios"]["net_margin"], "0.1")

    def test_spin_off_after_the_period_is_not_marked_at_its_year_end_price(self):
        """RTX separated Carrier and Otis on 03.04.2020. The FY2019 price of 31.12.2019
        still includes them, so the screener's mark for a later date does not apply.

        The vendor scaled that close down by the separated 37.0673%; the share basis
        restores it, as FY2019 results include both businesses: 40 / (1 - 0.370673)
        = 63.56, and P/E 63.56 / 2 = 31.78."""
        fiscal_2019 = (date(2019, 1, 1), date(2019, 12, 31))
        self.add_year(date(2020, 2, 7), fiscal_2019, company=4)
        self.add_close(date(2019, 12, 31), company=4)

        period = self.history(company_id=4, ticker="RTX")[0]

        self.assertEqual(period["spin_offs"], [])
        self.assertEqual(period["ratios"]["pe"], "31.78")

    def test_company_without_periods_has_an_empty_history(self):
        self.assertEqual(self.history(), [])


class AnnualRatioApiTests(ScreenerDatabaseTestCase):
    def test_years_come_newest_first_with_their_fiscal_year_label(self):
        """The label follows the annual financials table: a reviewed rule names FY2023
        for the second year's source; the first year has no fact, so no label. Numbers
        are decimal strings, as in every other response."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 2, 1), SECOND_YEAR)
        self.add_close(date(2023, 12, 29))
        self.add_revenue_fact(SECOND_YEAR, "1000", "a", date(2024, 2, 1))
        rules = {"periods": {"0000000001:2023-01-01:2023-12-31": {"fiscal_year": 2023}}}

        with patch("app.company_ratios.fiscal_rules", return_value=rules):
            response = self.client.get("/stocks/test/ratios/annual")

        self.assertEqual(response.status_code, 200, response.text)
        periods = response.json()
        self.assertEqual([period["fiscal_year"] for period in periods], [2023, None])
        self.assertEqual(periods[0]["period_end"], "2023-12-31")
        self.assertEqual(periods[0]["price"], "40")
        self.assertEqual(periods[0]["ratios"]["pe"], "20")

    def test_unknown_or_unpublished_company_returns_404(self):
        for ticker in ("MISSING", "HIDDEN"):
            with self.subTest(ticker=ticker):
                response = self.client.get(f"/stocks/{ticker}/ratios/annual")

                self.assertEqual(response.status_code, 404)

    def test_company_without_reports_has_an_empty_list(self):
        response = self.client.get("/stocks/TEST/ratios/annual")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
