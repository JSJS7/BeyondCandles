import threading
import time
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import delete
from test_screener import SECOND_YEAR, ScreenerDatabaseTestCase

from app import backtest_api
from app.models import Company, DailyPrice

# Two rankings: 29 Mar 2024 is valued at the close of 28 Mar, so the purchase is on 1 Apr;
# the second holding runs from 1 Apr 2025 to the end of the window and is partial.
RANKING_DATES = (date(2024, 3, 29), date(2025, 3, 31))
END_DATE = date(2025, 12, 31)
RANKING_SESSION = date(2024, 3, 28)
FIRST_PURCHASE = date(2024, 4, 1)
SECOND_RANKING_SESSION = date(2025, 3, 31)
SECOND_PURCHASE = date(2025, 4, 1)
LAST_SESSION = date(2025, 12, 31)
SESSIONS = (
    RANKING_SESSION,
    FIRST_PURCHASE,
    SECOND_RANKING_SESSION,
    SECOND_PURCHASE,
    LAST_SESSION,
)
# Companies ranked by net margin (revenue 1000): EEE 0.9 ... AAA 0.5.
RANKED = {
    6: ("AAA", 500),
    7: ("BBB", 600),
    8: ("CCC", 700),
    9: ("DDD", 800),
    10: ("EEE", 900),
}
# The fixture's own companies (TEST, JCI, RTX, KKR) have prices but no report: empty values.
UNREPORTED = (1, 2, 4, 5)
# The fixture has no growth facts, so the tests rank only ratios it can fill.
TEST_RATIOS = {"pe": True, "net_margin": False}


class BacktestApiTests(ScreenerDatabaseTestCase):
    def setUp(self):
        super().setUp()
        backtest_api.CACHED_RESULTS.clear()
        with self.database.begin() as connection:
            connection.execute(
                Company.__table__.insert(),
                [
                    self.company(number, ticker, f"{number:010d}")
                    for number, (ticker, _) in RANKED.items()
                ],
            )
        for number, (_, net_income) in RANKED.items():
            self.add_reported_year(number, net_income, date(2024, 2, 1))
            # Bought at 100, worth 100 + net income / 10 a year later: EEE +90%.
            self.add_closes(number, purchase=100, later=100 + net_income / 10)
        for number in UNREPORTED:
            self.add_closes(number, purchase=100, later=100)
        patches = (
            patch.object(backtest_api, "RANKING_DATES", RANKING_DATES),
            patch.object(backtest_api, "END_DATE", END_DATE),
            patch.dict("app.backtest.RATIO_LOWEST_FIRST", TEST_RATIOS, clear=True),
        )
        for active in patches:
            active.start()
            self.addCleanup(active.stop)

    def add_reported_year(self, company, net_income, known_from):
        """A complete year as the fixture's `add_year`, with the company's own net income."""
        for metric, value in (
            ("revenue", "1000"),
            ("net_income", str(net_income)),
            ("eps_diluted", "2"),
            ("equity", "500"),
            ("operating_cash_flow", "150"),
            ("shares_diluted", "50"),
            ("assets", "2000"),
        ):
            self.add_version(metric, value, known_from, SECOND_YEAR, company)

    def add_closes(self, company, purchase, later):
        """Close 40 on the ranking session, `purchase` on 1 Apr 2024, `later` afterwards."""
        prices = {
            RANKING_SESSION: 40,
            FIRST_PURCHASE: purchase,
            SECOND_RANKING_SESSION: later,
            SECOND_PURCHASE: later,
            LAST_SESSION: later,
        }
        for session in SESSIONS:
            self.add_close(session, str(prices[session]), company)

    def ratio(self, name):
        response = self.client.get(f"/backtest/{name}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["ratios"][0]

    def test_quintiles_hold_the_screener_ranking_bought_on_the_next_session(self):
        """Highest net margin first, one company per quintile; purchase on the session
        after the ranking session, sale on the next purchase, the last at the window end."""
        net_margin = self.ratio("net_margin")

        first, second = net_margin["periods"]
        self.assertEqual(first["buy_session"], "2024-04-01")
        self.assertEqual(first["sell_session"], "2025-04-01")
        self.assertFalse(first["partial"])
        self.assertEqual(second["buy_session"], "2025-04-01")
        self.assertEqual(second["sell_session"], "2025-12-31")
        self.assertTrue(second["partial"])
        self.assertEqual(first["coverage"]["ranked"], 5)
        self.assertEqual(first["coverage"]["empty"], 4)
        tickers = [quintile["members"][0]["ticker"] for quintile in first["quintiles"]]
        self.assertEqual(tickers, ["EEE", "DDD", "CCC", "BBB", "AAA"])
        best = first["quintiles"][0]["members"][0]
        self.assertEqual(best["value"], "0.9")
        self.assertEqual(best["period_return"], 0.9)
        self.assertEqual(best["market_value"], "2000")
        self.assertEqual(net_margin["full_years"], 1)
        # The fixture has no sessions between the purchases, and 31 Mar 2025 is only a day
        # before the sale, so the year is one "month": too little for a t statistic.
        self.assertEqual(net_margin["spread_months"], 1)
        self.assertIsNone(net_margin["spread_t"])

    def test_report_filed_on_the_ranking_session_waits_for_the_next_ranking(self):
        """M41: AAA restates its net income (500 -> 950) in a report filed on the ranking
        session itself. That close cannot reflect it, so the 2024 ranking keeps margin 0.5
        (last quintile) and only the 2025 ranking uses 0.95 (first quintile)."""
        self.add_version("net_income", "950", RANKING_SESSION, SECOND_YEAR, company=6)

        net_margin = self.ratio("net_margin")

        first, second = net_margin["periods"]
        first_last = first["quintiles"][4]["members"][0]
        second_best = second["quintiles"][0]["members"][0]
        self.assertEqual((first_last["ticker"], first_last["value"]), ("AAA", "0.5"))
        self.assertEqual((second_best["ticker"], second_best["value"]), ("AAA", "0.95"))

    def test_report_filed_on_the_purchase_session_changes_neither_ranking_nor_price(
        self,
    ):
        """BBB restates its net income (600 -> 950) on the purchase session: the ranking
        was made on the session before, so it keeps margin 0.6 (fourth quintile), and
        the purchase is still at that session's close of 100."""
        self.add_version("net_income", "950", FIRST_PURCHASE, SECOND_YEAR, company=7)

        net_margin = self.ratio("net_margin")

        first, second = net_margin["periods"]
        fourth = first["quintiles"][3]["members"][0]
        self.assertEqual((fourth["ticker"], fourth["value"]), ("BBB", "0.6"))
        # Bought at 100 on 1 Apr 2024, worth 100 + 600 / 10 a year later.
        self.assertEqual(fourth["period_return"], 0.6)
        second_best = second["quintiles"][0]["members"][0]
        self.assertEqual((second_best["ticker"], second_best["value"]), ("BBB", "0.95"))

    def test_ranking_compares_numbers_not_text(self):
        """Regression (18.11.2026): the first saved result ranked P/E as text, so "11.3"
        came before "3.59" and quintile 1 held mid-priced companies. With EPS restated to
        8, 4, 2, 1 and 0.5 the P/E are 5, 10, 20, 40 and 80 at the close of 40; as text
        "10" < "20" < "40" < "5" < "80" would put AAA fourth."""
        restated_eps = {6: "8", 7: "4", 8: "2", 9: "1", 10: "0.5"}
        for company, eps in restated_eps.items():
            self.add_version("eps_diluted", eps, date(2024, 2, 2), SECOND_YEAR, company)

        pe = self.ratio("pe")

        first = pe["periods"][0]
        tickers = [quintile["members"][0]["ticker"] for quintile in first["quintiles"]]
        values = [quintile["members"][0]["value"] for quintile in first["quintiles"]]
        self.assertEqual(tickers, ["AAA", "BBB", "CCC", "DDD", "EEE"])
        self.assertEqual(values, ["5", "10", "20", "40", "80"])

    def test_summary_lists_every_ratio_without_companies(self):
        """GET /backtest leaves the members out; the catalogue holds all nine companies."""
        response = self.client.get("/backtest")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            [item["ratio"] for item in body["ratios"]], ["pe", "net_margin"]
        )
        for item in body["ratios"]:
            for quintile in item["periods"][0]["quintiles"]:
                self.assertIsNone(quintile["members"])
        self.assertEqual(body["catalogue"][0]["count"], 9)
        self.assertEqual(body["trade_cost"], 0.001)

    def test_unknown_or_left_out_ratio_is_not_found(self):
        """Operating margin is a screener ratio, but the backtest leaves it out (D4)."""
        for name in ("operating_margin", "price"):
            with self.subTest(name=name):
                response = self.client.get(f"/backtest/{name}")
                self.assertEqual(response.status_code, 404)

    def test_missing_price_stops_the_backtest(self):
        """A company without a close on a sale session must not drop out silently."""
        with self.database.begin() as connection:
            connection.execute(
                delete(DailyPrice.__table__).where(
                    DailyPrice.__table__.c.company_id == 8,
                    DailyPrice.__table__.c.date == LAST_SESSION,
                )
            )

        with self.assertRaises(ValueError):
            self.client.get("/backtest/net_margin")


class BacktestCacheTests(unittest.TestCase):
    def test_concurrent_first_requests_compute_the_result_once(self):
        """The page asks for the summary and one ratio at once after the API starts.

        Both requests find no result yet; the second must wait for the first one's result
        instead of running the five-second computation again (18.11.2026: 8.3 s, not 5 s).
        """
        # Arrange: a slow computation that counts its runs, and one shared database engine.
        backtest_api.CACHED_RESULTS.clear()
        self.addCleanup(backtest_api.CACHED_RESULTS.clear)
        runs = []

        def slow_load_periods(connection, ranking_dates, end_date):
            time.sleep(0.2)
            return ["periods"]

        def counting_run_backtest(periods, financial):
            runs.append(periods)
            return "result"

        connection = SimpleNamespace(engine="engine")
        start_together = threading.Barrier(2)
        answers = []

        def request():
            start_together.wait()
            answers.append(backtest_api.cached_backtest(connection))

        # Act: two requests at the same moment.
        with (
            patch.object(backtest_api, "load_periods", slow_load_periods),
            patch.object(backtest_api, "run_backtest", counting_run_backtest),
            patch.object(backtest_api, "financial_tickers", set),
        ):
            threads = [threading.Thread(target=request) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        # Assert: one computation, and both requests got its result.
        self.assertEqual(len(runs), 1)
        self.assertEqual(answers, [(["periods"], "result")] * 2)


if __name__ == "__main__":
    unittest.main()
