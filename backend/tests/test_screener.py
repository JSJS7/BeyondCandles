"""Screener contract and date rules; fixtures never use the application database."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_connection
from app.fiscal_periods import fiscal_rules
from app.growth import (
    ReportYears,
    SeriesCell,
    annual_report_columns,
    one_report_growth,
    source_coefficients,
    year_before,
)
from app.main import app
from app.models import AnnualMetricVersion, Company, DailyPrice, FinancialFact
from app.ratios import ExchangeableUnits

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

    def test_earlier_year_ends_350_to_380_days_before(self):
        """K19: both limits count, one day outside does not. Every period of the data
        is 364-371 days apart, so the window holds 52/53-week years and never two."""
        later = (date(2024, 1, 1), date(2024, 12, 31))

        def earlier_than_later_by(days):
            end = date.fromordinal(later[1].toordinal() - days)
            return (date(end.year, 1, 1), end)

        for days, found in ((349, False), (350, True), (380, True), (381, False)):
            with self.subTest(days=days):
                candidate = earlier_than_later_by(days)
                periods = [candidate, later]

                result = year_before(periods, later)

                self.assertEqual(result, candidate if found else None)

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
        # The restated 90 is kept for the page, which shows it next to the table's 100.
        self.assertEqual(
            growth.report,
            ReportYears(
                "new", date(2024, 2, 1), "0000000001", Decimal(90), Decimal(99)
            ),
        )

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

    def test_balance_sheet_instants_of_one_report_give_growth(self):
        """Equity is a balance at a date, and an annual report states two year ends, so
        its change is read from one report like a full-year amount."""
        facts = [
            dict(
                report_fact("StockholdersEquity", end, value, "new", date(2024, 2, 1)),
                metric="equity",
                period_kind="instant",
                period_start=end,
            )
            for end, value in ((self.EARLIER_END, "200"), (self.LATER_END, "250"))
        ]

        growth = one_report_growth(
            self.reports(*facts),
            SeriesCell(self.EARLIER_END, Decimal(200), {"StockholdersEquity": 1}),
            SeriesCell(self.LATER_END, Decimal(250), {"StockholdersEquity": 1}),
        )

        self.assertEqual(growth.value, Decimal("0.25"))
        self.assertEqual(growth.report.accession, "new")

    def test_sources_sharing_one_tag_give_no_coefficients(self):
        """ABBV's parent equity is group equity minus its noncontrolling part, and both
        sources carry the group-equity tag (the part is a statement position). A report
        column holds one value per tag, so it cannot rebuild the cell. Before this rule
        the two signs merged into {tag: -1}, the report value turned negative and every
        such year was marked as a series break."""
        tag = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        sources = [{"tag": tag, "value": "3364"}, {"tag": tag, "value": "39"}]

        self.assertEqual(source_coefficients(Decimal(3325), sources), {})

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
    def make_database(self):
        """An empty in-memory schema; test_api_postgres runs these tests on PostgreSQL."""
        database = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(database)
        return database

    def setUp(self):
        self.database = self.make_database()
        with self.database.begin() as connection:
            connection.execute(
                Company.__table__.insert(),
                [
                    self.company(1, "TEST", "0000000001"),
                    self.company(2, "JCI", "0000000002"),
                    self.company(3, "HIDDEN", None),
                    self.company(4, "RTX", "0000000004"),
                    self.company(5, "KKR", "0000000005"),
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
        self,
        metric,
        value,
        known_from,
        period=SECOND_YEAR,
        company=1,
        status="ready",
        form="10-K",
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
                    "form": form,
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

    def add_revenue_fact(self, period, value, accession, filed):
        """A revenue fact of one annual report, under the tag the stored cells name."""
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(),
                {
                    "company_id": 1,
                    "source_cik": "0000000001",
                    "history_eligible": True,
                    "metric": "revenue",
                    "taxonomy": "us-gaap",
                    "tag": "Tag",
                    "unit": "USD",
                    "value": Decimal(value),
                    "period_start": period[0],
                    "period_end": period[1],
                    "filed": filed,
                    "accession": accession,
                    "form": "10-K",
                    "fiscal_period": "FY",
                    "period_kind": "annual",
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
        self.assertEqual(row["latest"]["shares_basis"], "weighted_average")
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

    def test_report_filed_on_the_session_counts_from_the_next_session(self):
        """M41: most annual reports are accepted after the close, and a filing date has
        no time of day, so the close of the filing day cannot reflect the report. The
        earlier year is valued that day; the new report counts from the next session."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 3, 27), SECOND_YEAR)
        self.add_close(date(2024, 3, 27))
        self.add_close(date(2024, 3, 28))

        filing_day = self.row(date="2024-03-27")
        next_session = self.row(date="2024-03-28")

        self.assertEqual(filing_day["latest"]["period_end"], "2022-12-31")
        self.assertEqual(filing_day["latest"]["price_date"], "2024-03-27")
        self.assertEqual(next_session["latest"]["period_end"], "2023-12-31")

    def test_weekend_date_uses_reports_filed_before_the_last_session(self):
        """On a Sunday the price is Friday's close, so a report filed on Friday after the
        close must not be paired with it."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 3, 22), SECOND_YEAR)
        self.add_close(date(2024, 3, 22))

        sunday = self.row(date="2024-03-24")

        self.assertEqual(sunday["latest"]["price_date"], "2024-03-22")
        self.assertEqual(sunday["latest"]["period_end"], "2022-12-31")

    def test_growth_never_reads_a_report_filed_after_the_date(self):
        """K21: growth comes from the newest report known before the session. A later
        report that restates the earlier year (800 instead of 1000) would turn 0% into
        +25%: knowledge from the future that no stored version reveals."""
        self.add_year(date(2023, 2, 1), FIRST_YEAR)
        self.add_year(date(2024, 2, 1), SECOND_YEAR)
        self.add_revenue_fact(FIRST_YEAR, "1000", "known", date(2024, 2, 1))
        self.add_revenue_fact(SECOND_YEAR, "1000", "known", date(2024, 2, 1))
        self.add_revenue_fact(FIRST_YEAR, "800", "later", date(2025, 2, 1))
        self.add_revenue_fact(SECOND_YEAR, "1000", "later", date(2025, 2, 1))
        self.add_close(date(2024, 3, 28))

        row = self.row(date="2024-03-29")

        self.assertEqual(row["latest"]["ratios"]["revenue_growth"], "0")
        self.assertFalse(row["latest"]["revenue_basis_break"])

    def test_quarterly_restatement_does_not_replace_the_annual_report(self):
        """W39: a 10-Q restating equity under a new standard (500 to 1000) arrives
        months before the 10-K restates income, so ratios keep the annual report's
        equity until then: P/B stays 2000 / 500 and equity / assets 500 / 2000."""
        self.add_year(date(2024, 2, 1))
        self.add_version("equity", "1000", date(2024, 4, 30), form="10-Q")
        self.add_close(date(2024, 6, 28))

        row = self.row(date="2024-06-28")

        self.assertEqual(row["latest"]["ratios"]["pb"], "4")
        self.assertEqual(row["latest"]["ratios"]["equity_to_assets"], "0.25")

    def test_value_known_only_from_a_quarterly_report_is_used(self):
        """A cell without any annual version (a predecessor filed the 10-K) takes the
        10-Q value, so the ratio is not left empty for a year."""
        self.add_year(date(2024, 2, 1))
        self.add_version("capex", "40", date(2024, 4, 30), form="10-Q")
        self.add_close(date(2024, 6, 28))

        row = self.row(date="2024-06-28")

        # (150 - 40) / 1000
        self.assertEqual(row["latest"]["ratios"]["fcf_margin"], "0.11")

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

    def test_a_close_seven_days_before_the_date_is_still_its_price(self):
        """K19: the lookback covers seven days (longer than any market closure), so a
        close exactly a week old counts and one eight days old does not."""
        self.add_year(date(2024, 2, 1))
        self.add_close(date(2024, 3, 22))

        week_old = self.row(date="2024-03-29")
        eight_days_old = self.row(date="2024-03-30")

        self.assertEqual(week_old["latest"]["price_date"], "2024-03-22")
        self.assertIsNone(eight_days_old["latest"]["price"])

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

    def test_spin_off_is_marked_until_a_period_after_it_is_known(self):
        """RTX separated Carrier and Otis on 03.04.2020 (37% of the price by the vendor's
        ratio). FY2019 results with a later price are marked, ratios unchanged; the
        FY2020 report, whose period includes the event, ends the mark."""
        fiscal_2019 = (date(2019, 1, 1), date(2019, 12, 31))
        fiscal_2020 = (date(2020, 1, 1), date(2020, 12, 31))
        self.add_year(date(2020, 2, 7), fiscal_2019, company=4)
        self.add_year(date(2021, 2, 8), fiscal_2020, company=4)
        for day in (date(2020, 3, 31), date(2020, 6, 30), date(2021, 3, 31)):
            self.add_close(day, company=4)

        before_event = self.row("RTX", date="2020-03-31")
        in_window = self.row("RTX", date="2020-06-30")
        after_report = self.row("RTX", date="2021-03-31")

        self.assertEqual(before_event["latest"]["spin_offs"], [])
        self.assertEqual(
            in_window["latest"]["spin_offs"],
            [{"event_date": "2020-04-03", "separated_fraction": "0.370673"}],
        )
        self.assertEqual(in_window["latest"]["ratios"]["pe"], "20")
        self.assertEqual(after_report["latest"]["spin_offs"], [])

    def test_spin_off_without_a_price_is_not_marked(self):
        """With no price of the date there are no price ratios to distort."""
        self.add_year(date(2020, 2, 7), (date(2019, 1, 1), date(2019, 12, 31)), 4)

        row = self.row("RTX", date="2020-06-30")

        self.assertEqual(row["latest"]["spin_offs"], [])

    def test_exchangeable_units_change_ps_and_pb_of_their_period_only(self):
        """M52: market value 40 x 50 = 2000. Units outside the count (25) belong to the
        whole group, so P/S is 40 x 75 / 1000 = 3; units inside it (10) are not listed
        shares, so P/B is 40 x 40 / 500 = 3.2. Another period and company are unchanged."""
        fiscal_2021 = (date(2021, 1, 1), date(2021, 12, 31))
        fiscal_2022 = (date(2022, 1, 1), date(2022, 12, 31))
        self.add_year(date(2022, 2, 28), fiscal_2021, company=5)
        self.add_year(date(2023, 2, 27), fiscal_2022, company=5)
        self.add_year(date(2022, 2, 28), fiscal_2021, company=1)
        for day in (date(2022, 3, 31), date(2023, 3, 31)):
            self.add_close(day, company=5)
            self.add_close(day, company=1)
        units = {
            ("KKR", fiscal_2021[1]): ExchangeableUnits(
                outside_count=Decimal(25), inside_count=Decimal(10)
            )
        }

        with patch("app.screener.exchangeable_units", return_value=units):
            listed_period = self.row("KKR", date="2022-03-31")
            later_period = self.row("KKR", date="2023-03-31")
            other_company = self.row("TEST", date="2022-03-31")

        self.assertTrue(listed_period["latest"]["exchangeable_units"])
        self.assertEqual(listed_period["latest"]["ratios"]["ps"], "3")
        self.assertEqual(listed_period["latest"]["ratios"]["pb"], "3.2")
        self.assertEqual(listed_period["latest"]["ratios"]["pe"], "20")
        self.assertFalse(later_period["latest"]["exchangeable_units"])
        self.assertEqual(later_period["latest"]["ratios"]["ps"], "2")
        self.assertFalse(other_company["latest"]["exchangeable_units"])
        self.assertEqual(other_company["latest"]["ratios"]["ps"], "2")

    def test_discontinued_operations_mark_the_period_from_their_report(self):
        """M46: JNJ's FY2023 net income held 62% from Kenvue. The mark follows the value
        known on the date (a restating report adds it later) and changes no ratio."""
        self.add_year(date(2024, 2, 16))
        self.add_version("discontinued_income", "4", date(2024, 2, 16))
        self.add_version("discontinued_income", "62", date(2024, 5, 1), form="10-K/A")
        for day in (date(2024, 3, 28), date(2024, 6, 28)):
            self.add_close(day)

        below_limit = self.row(date="2024-03-29")
        above_limit = self.row(date="2024-06-28")

        self.assertFalse(below_limit["latest"]["discontinued_operations"])
        self.assertTrue(above_limit["latest"]["discontinued_operations"])
        self.assertEqual(above_limit["latest"]["ratios"]["pe"], "20")

    def test_cover_count_values_the_shares_from_its_report(self):
        """M47: the cover count known on the date sets P/B and P/S; an amended cover
        replaces it from its own report (KR 2015), and P/E does not depend on it."""
        self.add_year(date(2024, 2, 16))
        self.add_version("shares_outstanding", "60", date(2024, 2, 16))
        self.add_version("shares_outstanding", "55", date(2024, 5, 1), form="10-K/A")
        for day in (date(2024, 3, 28), date(2024, 6, 28)):
            self.add_close(day)

        first_cover = self.row(date="2024-03-29")["latest"]
        amended_cover = self.row(date="2024-06-28")["latest"]

        self.assertEqual(first_cover["shares_basis"], "outstanding")
        self.assertEqual(first_cover["ratios"]["pb"], "4.8")
        self.assertEqual(first_cover["ratios"]["ps"], "2.4")
        self.assertEqual(amended_cover["ratios"]["pb"], "4.4")
        self.assertEqual(amended_cover["ratios"]["pe"], "20")

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
