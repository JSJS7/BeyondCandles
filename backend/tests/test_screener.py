"""Screener contract and date rules; fixtures never use the application database."""

import unittest
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_connection
from app.fiscal_periods import fiscal_rules
from app.growth import (
    SeriesCell,
    annual_report_columns,
    one_report_growth,
    source_coefficients,
)
from app.main import app
from app.models import AnnualMetricVersion, Company, DailyPrice

FIRST_YEAR = (date(2022, 1, 1), date(2022, 12, 31))
SECOND_YEAR = (date(2023, 1, 1), date(2023, 12, 31))


def report_fact(tag, end, value, accession, filed):
    return {
        "metric": "revenue",
        "tag": tag,
        "value": Decimal(value),
        "period_start": date(end.year, 1, 1),
        "period_end": end,
        "filed": filed,
        "accession": accession,
        "form": "10-K",
        "period_kind": "annual",
        "source_cik": "0000000001",
        "history_eligible": True,
    }


class GrowthTests(unittest.TestCase):
    EARLIER_END = date(2022, 12, 31)
    LATER_END = date(2023, 12, 31)

    def reports(self, *facts):
        return annual_report_columns(list(facts), fiscal_rules())

    def test_growth_comes_from_one_report_and_marks_the_series_break(self):
        """Later report restates 2022 to 90 and states 2023 as 99, so growth is +10%.

        The series joins 100 from the old report with 99, which looks like -1%, a
        difference above 5%, so the year gets a marker (AIG 2022 in real data).
        """
        reports = self.reports(
            report_fact("Revenues", self.EARLIER_END, "100", "old", date(2023, 2, 1)),
            report_fact("Revenues", self.LATER_END, "99", "new", date(2024, 2, 1)),
            report_fact("Revenues", self.EARLIER_END, "90", "new", date(2024, 2, 1)),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(100), {"Revenues": 1}),
            SeriesCell(self.LATER_END, Decimal(99), {"Revenues": 1}),
        )

        self.assertEqual(growth.value, Decimal("0.1"))
        self.assertTrue(growth.basis_break)

    def test_no_marker_when_series_agrees_with_the_report(self):
        """No restatement: series and report show the same change."""
        reports = self.reports(
            report_fact("Revenues", self.LATER_END, "110", "new", date(2024, 2, 1)),
            report_fact("Revenues", self.EARLIER_END, "100", "new", date(2024, 2, 1)),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(100), {"Revenues": 1}),
            SeriesCell(self.LATER_END, Decimal(110), {"Revenues": 1}),
        )

        self.assertEqual(growth.value, Decimal("0.1"))
        self.assertFalse(growth.basis_break)

    def test_earlier_years_tag_is_used_when_the_tag_changed_later(self):
        """A later report's new tag (ASC 606) must not hide a report stating both years."""
        reports = self.reports(
            report_fact(
                "SalesRevenueNet", self.LATER_END, "110", "a", date(2024, 2, 1)
            ),
            report_fact(
                "SalesRevenueNet", self.EARLIER_END, "100", "a", date(2024, 2, 1)
            ),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(100), {"SalesRevenueNet": 1}),
            SeriesCell(self.LATER_END, Decimal(110), {"RevenueFromContract": 1}),
        )

        self.assertEqual(growth.value, Decimal("0.1"))

    def test_calculated_value_needs_all_its_components_in_the_report(self):
        """A bank's revenue is the sum of two rows, in both years: 130 / 100."""
        filed = date(2024, 2, 1)
        reports = self.reports(
            report_fact("Interest", self.LATER_END, "80", "a", filed),
            report_fact("Fees", self.LATER_END, "50", "a", filed),
            report_fact("Interest", self.EARLIER_END, "60", "a", filed),
            report_fact("Fees", self.EARLIER_END, "40", "a", filed),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(100), {"Interest": 1, "Fees": 1}),
            SeriesCell(self.LATER_END, Decimal(130), {"Interest": 1, "Fees": 1}),
        )

        self.assertEqual(growth.value, Decimal("0.3"))

    def test_without_a_common_report_growth_is_empty(self):
        """Decision 04.11.2026: the series is never a fallback, even if it looks fine."""
        reports = self.reports(
            report_fact("Revenues", self.EARLIER_END, "100", "old", date(2023, 2, 1)),
            report_fact("Revenues", self.LATER_END, "110", "new", date(2024, 2, 1)),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(100), {"Revenues": 1}),
            SeriesCell(self.LATER_END, Decimal(110), {"Revenues": 1}),
        )

        self.assertIsNone(growth.value)
        self.assertFalse(growth.basis_break)

    def test_non_positive_base_gives_no_growth(self):
        """Change from a loss has no meaningful percentage."""
        reports = self.reports(
            report_fact("Revenues", self.LATER_END, "5", "a", date(2024, 2, 1)),
            report_fact("Revenues", self.EARLIER_END, "-2", "a", date(2024, 2, 1)),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(-2), {"Revenues": 1}),
            SeriesCell(self.LATER_END, Decimal(5), {"Revenues": 1}),
        )

        self.assertIsNone(growth.value)

    def test_change_into_a_loss_gives_no_growth(self):
        """A fall from 2 to -4 is not -300%; the size only reflects a small base (INTC)."""
        reports = self.reports(
            report_fact("Revenues", self.LATER_END, "-4", "a", date(2024, 2, 1)),
            report_fact("Revenues", self.EARLIER_END, "2", "a", date(2024, 2, 1)),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(2), {"Revenues": 1}),
            SeriesCell(self.LATER_END, Decimal(-4), {"Revenues": 1}),
        )

        self.assertIsNone(growth.value)
        self.assertFalse(growth.basis_break)

    def test_sign_change_between_series_and_report_is_marked(self):
        """The series moves from -10 to 99 while the report says 90 to 99: a clear break."""
        reports = self.reports(
            report_fact("Revenues", self.LATER_END, "99", "new", date(2024, 2, 1)),
            report_fact("Revenues", self.EARLIER_END, "90", "new", date(2024, 2, 1)),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(-10), {"Revenues": 1}),
            SeriesCell(self.LATER_END, Decimal(99), {"Revenues": 1}),
        )

        self.assertEqual(growth.value, Decimal("0.1"))
        self.assertTrue(growth.basis_break)

    def test_calculated_value_subtracts_what_its_cell_subtracts(self):
        """Regression: PM's revenue is sales minus excise, so growth is 26685 / 26794 - 1.

        Numbers from the 10-K for 2017. Adding the two rows gave +1.8% instead of -0.41%.
        """
        filed = date(2018, 2, 13)
        sources = [
            {"tag": "SalesRevenueNet", "value": "74953000000"},
            {"tag": "ExciseAndSalesTaxes", "value": "48268000000"},
        ]
        coefficients = source_coefficients(Decimal("26685000000"), sources)
        self.assertEqual(
            coefficients, {"SalesRevenueNet": 1, "ExciseAndSalesTaxes": -1}
        )
        reports = self.reports(
            report_fact("SalesRevenueNet", self.LATER_END, "74953", "a", filed),
            report_fact("ExciseAndSalesTaxes", self.LATER_END, "48268", "a", filed),
            report_fact("SalesRevenueNet", self.EARLIER_END, "73908", "a", filed),
            report_fact("ExciseAndSalesTaxes", self.EARLIER_END, "47114", "a", filed),
        )

        growth = one_report_growth(
            reports,
            SeriesCell(self.EARLIER_END, Decimal(26794), coefficients),
            SeriesCell(self.LATER_END, Decimal(26685), coefficients),
        )

        self.assertAlmostEqual(float(growth.value), 26685 / 26794 - 1)

    def test_sources_that_do_not_rebuild_the_cell_give_no_coefficients(self):
        """No choice of signs gives the value, so the calculation is not understood."""
        sources = [{"tag": "A", "value": "10"}, {"tag": "B", "value": "3"}]

        self.assertEqual(source_coefficients(Decimal(100), sources), {})

    def test_ineligible_and_non_annual_facts_are_left_out(self):
        """A predecessor's report that dates versions does not define growth."""
        predecessor = {
            **report_fact("Revenues", self.LATER_END, "1", "p", date(2024, 2, 1)),
            "history_eligible": False,
        }
        quarter = {
            **report_fact("Revenues", self.LATER_END, "2", "q", date(2024, 2, 1)),
            "period_kind": "quarter",
        }

        self.assertEqual(self.reports(predecessor, quarter), [])


class ScreenerApiTests(unittest.TestCase):
    def setUp(self):
        self.database = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.database)
        with self.database.begin() as connection:
            connection.execute(
                Company.__table__.insert(),
                [
                    self.company(1, "TEST", "0000000001"),
                    self.company(2, "JCI", "0000000002"),
                    self.company(3, "HIDDEN", None),
                ],
            )

        def test_connection():
            with self.database.connect() as connection:
                yield connection

        app.dependency_overrides[get_connection] = test_connection
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.database.dispose()

    def company(self, identifier, ticker, cik):
        return {
            "id": identifier,
            "ticker": ticker,
            "name": ticker.title(),
            "exchange": "NYSE",
            "sector": "Technology",
            "currency": "USD",
            "cik": cik,
        }

    def add_version(
        self, metric, value, known_from, period=SECOND_YEAR, company=1, status="ready"
    ):
        details = {"status": status, "selected_sources": [{"tag": "Tag"}]}
        if metric == "operating_cash_flow":
            details["scope"] = "total"
        if metric == "eps_diluted":
            details["eps_basis"] = "diluted"
        with self.database.begin() as connection:
            connection.execute(
                AnnualMetricVersion.__table__.insert(),
                {
                    "company_id": company,
                    "period_start": period[0],
                    "period_end": period[1],
                    "metric": metric,
                    "known_from": known_from,
                    "accession": "a",
                    "form": "10-K",
                    "status": status,
                    "confirmation": "approved" if status == "ready" else None,
                    "value": None if value is None else Decimal(value),
                    "details": details,
                },
            )

    def add_year(self, known_from, period=SECOND_YEAR, company=1):
        """A complete reviewed year: market value at price 40 is 2000 with 50 shares."""
        for metric, value in (
            ("revenue", "1000"),
            ("net_income", "100"),
            ("eps_diluted", "2"),
            ("equity", "500"),
            ("operating_cash_flow", "150"),
            ("shares_diluted", "50"),
            ("assets", "2000"),
        ):
            self.add_version(metric, value, known_from, period, company)

    def add_close(self, day, close="40", company=1):
        with self.database.begin() as connection:
            connection.execute(
                DailyPrice.__table__.insert(),
                {
                    "company_id": company,
                    "date": day,
                    "open": Decimal(close),
                    "high": Decimal(close),
                    "low": Decimal(close),
                    "close": Decimal(close),
                    "adjusted_close": Decimal(close),
                    "volume": 1,
                },
            )

    def row(self, ticker="TEST", **parameters):
        response = self.client.get("/screener", params=parameters)
        self.assertEqual(response.status_code, 200, response.text)
        rows = {row["ticker"]: row for row in response.json()["rows"]}
        return rows[ticker]

    def test_row_has_ratios_and_dates_as_text_numbers(self):
        """A complete year gives known ratios; the unpublished company is not listed."""
        self.add_year(date(2024, 2, 1))
        self.add_close(date(2024, 3, 28))

        row = self.row(date="2024-03-29")

        self.assertEqual(row["latest"]["period_end"], "2023-12-31")
        self.assertEqual(row["latest"]["price_date"], "2024-03-28")
        self.assertEqual(row["latest"]["shares_basis"], "reported")
        self.assertEqual(row["latest"]["ratios"]["pe"], "20")
        self.assertEqual(row["latest"]["ratios"]["pb"], "4")
        self.assertEqual(row["latest"]["ratios"]["net_margin"], "0.1")
        self.assertIsNone(row["latest"]["ratios"]["roe"])
        response = self.client.get("/screener", params={"date": "2024-03-29"})
        tickers = [item["ticker"] for item in response.json()["rows"]]
        self.assertNotIn("HIDDEN", tickers)

    def test_nothing_filed_after_the_date_is_used(self):
        """Point in time: a value restated later and a newer year are not known yet."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_version("revenue", "2000", date(2024, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 2, 1), SECOND_YEAR)
        self.add_close(date(2023, 12, 29))

        row = self.row(date="2023-12-31")

        self.assertEqual(row["latest"]["period_end"], "2022-12-31")
        self.assertEqual(row["latest"]["ratios"]["net_margin"], "0.1")

    def test_previous_year_is_found_by_date_for_average_ratios(self):
        """ROE averages closing equity of the year that ended about a year earlier."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 2, 1), SECOND_YEAR)
        self.add_close(date(2024, 3, 28))

        row = self.row(date="2024-03-29")

        self.assertEqual(row["latest"]["ratios"]["roe"], "0.2")

    def test_latest_version_needing_review_stays_empty(self):
        """An unconfirmed latest version hides the older value, as in the as_of table."""
        self.add_year(date(2024, 2, 1))
        self.add_version("net_income", None, date(2024, 3, 1), status="needs_review")
        self.add_close(date(2024, 3, 28))

        row = self.row(date="2024-03-29")

        self.assertIsNone(row["latest"]["ratios"]["net_margin"])
        self.assertEqual(row["latest"]["ratios"]["ps"], "2")

    def test_automatic_measure_alone_does_not_create_a_period(self):
        """A newer period known only from an automatic tag stays invisible."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_version("assets", "9", date(2024, 2, 1), SECOND_YEAR)
        self.add_close(date(2024, 3, 28))

        row = self.row(date="2024-03-29")

        self.assertEqual(row["latest"]["period_end"], "2022-12-31")

    def test_company_without_a_known_report_is_listed_empty(self):
        """It stays in the list so the reader can count what is missing."""
        row = self.row(date="2024-03-29")

        self.assertIsNone(row["latest"])

    def test_old_price_is_not_presented_as_the_price_of_the_date(self):
        """A close over a week before the date gives no price ratios."""
        self.add_year(date(2024, 2, 1))
        self.add_close(date(2024, 3, 1))

        row = self.row(date="2024-03-29")

        self.assertIsNone(row["latest"]["price"])
        self.assertIsNone(row["latest"]["ratios"]["pe"])
        self.assertEqual(row["latest"]["ratios"]["net_margin"], "0.1")

    def test_jci_gets_no_price_ratios_until_a_period_after_the_merger(self):
        """JCI's price after 02.09.2016 is the new company's, FY2015 the old company's."""
        fiscal_2015 = (date(2014, 10, 1), date(2015, 9, 30))
        fiscal_2016 = (date(2015, 10, 1), date(2016, 9, 30))
        self.add_year(date(2015, 11, 18), fiscal_2015, company=2)
        self.add_year(date(2016, 12, 1), fiscal_2016, company=2)
        self.add_close(date(2016, 9, 30), company=2)
        self.add_close(date(2017, 3, 31), company=2)

        before_report = self.row("JCI", date="2016-09-30")
        after_report = self.row("JCI", date="2017-03-31")

        self.assertIsNone(before_report["latest"]["ratios"]["pe"])
        self.assertEqual(after_report["latest"]["ratios"]["pe"], "20")

    def test_date_outside_the_window_is_rejected(self):
        """Before every company has a report, or after prices end, there is no screen."""
        for day in ("2016-03-30", "2026-01-01"):
            with self.subTest(day=day):
                response = self.client.get("/screener", params={"date": day})
                self.assertEqual(response.status_code, 422)

    def test_default_date_is_the_end_of_prices(self):
        """Without a date the screen uses the last day of the data window."""
        response = self.client.get("/screener")

        self.assertEqual(response.json()["valuation_date"], "2025-12-31")


if __name__ == "__main__":
    unittest.main()
