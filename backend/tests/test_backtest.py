import hashlib
import random
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app.backtest import (
    FINANCIAL_EXCLUDED_RATIOS,
    RATIO_LOWEST_FIRST,
    Candidate,
    HoldingPeriod,
    draw_seed,
    flagged_ratios,
    month_checkpoints,
    monthly_returns,
    quintile_sizes,
    random_mean_returns,
    ranking,
    run_backtest,
    stouffer,
    t_statistic,
    turnover,
)


def candidate(ticker, ratio_values=None, **marks):
    """A company whose ratios are all empty except the given ones."""
    values = {ratio: None for ratio in RATIO_LOWEST_FIRST}
    for ratio, value in (ratio_values or {}).items():
        values[ratio] = Decimal(str(value))
    return Candidate(ticker=ticker, values=values, **marks)


def known_example():
    """Two years, six companies ranked by P/E; the expected numbers are worked by hand.

    Year 1: F has no P/E, so A..E form one quintile each (A cheapest).
    Year 2: all six are ranked F, B | E | C | D | A, so quintile 1 holds two companies.
    """
    first_year = HoldingPeriod(
        ranking_date=date(2017, 3, 31),
        buy_session=date(2017, 4, 3),
        sell_session=date(2018, 4, 2),
        candidates=[
            candidate("A", {"pe": 10}, market_value=Decimal(100)),
            candidate("B", {"pe": 20}, market_value=Decimal(200)),
            candidate("C", {"pe": 30}),
            candidate("D", {"pe": 40}),
            candidate("E", {"pe": 50}),
            candidate("F"),
        ],
        returns={"A": 0.10, "B": 0.20, "C": 0.0, "D": -0.10, "E": 0.50, "F": 0.05},
    )
    second_year = HoldingPeriod(
        ranking_date=date(2018, 3, 31),
        buy_session=date(2018, 4, 2),
        sell_session=date(2019, 4, 1),
        candidates=[
            candidate("A", {"pe": 50}),
            candidate("B", {"pe": 10}, market_value=Decimal(300)),
            candidate("C", {"pe": 30}),
            candidate("D", {"pe": 40}),
            candidate("E", {"pe": 20}),
            candidate("F", {"pe": 5}, market_value=Decimal(100)),
        ],
        returns={"A": 0.0, "B": 0.10, "C": 0.20, "D": -0.20, "E": 0.0, "F": 0.30},
    )
    return [first_year, second_year]


class KnownExampleTests(unittest.TestCase):
    def setUp(self):
        result = run_backtest(known_example(), financial_tickers=set(), ratios=["pe"])
        self.result = result
        self.pe = result.ratios[0]

    def test_quintiles_follow_the_ranking_each_year(self):
        """Cheapest P/E first; the company without a value is counted as empty."""
        first, second = self.pe.periods

        first_holdings = [quintile.portfolio.tickers for quintile in first.quintiles]
        second_holdings = [quintile.portfolio.tickers for quintile in second.quintiles]

        self.assertEqual(first_holdings, [("A",), ("B",), ("C",), ("D",), ("E",)])
        self.assertEqual(first.coverage.ranked, 5)
        self.assertEqual(first.coverage.empty, 1)
        self.assertEqual(second_holdings, [("F", "B"), ("E",), ("C",), ("D",), ("A",)])

    def test_returns_turnover_and_costs_of_quintile_one(self):
        """Year 1: buy A from cash (turnover 1): net (1 - 0.001) * 1.10 - 1 = 0.0989.

        Year 2: sell all of A (weight 1) and buy F and B at 0.5 each: turnover 2.
        Gross (0.30 + 0.10) / 2 = 0.20, net (1 - 0.002) * 1.20 - 1 = 0.1976.
        """
        first, second = (period.quintiles[0].portfolio for period in self.pe.periods)

        self.assertAlmostEqual(first.gross_return, 0.10)
        self.assertAlmostEqual(first.turnover, 1.0)
        self.assertAlmostEqual(first.net_return, 0.0989)
        self.assertAlmostEqual(second.gross_return, 0.20)
        self.assertAlmostEqual(second.turnover, 2.0)
        self.assertAlmostEqual(second.net_return, 0.1976)

    def test_summary_compounds_the_years(self):
        """Gross 1.10 * 1.20 - 1 = 0.32; net 1.0989 * 1.1976 - 1 = 0.31604264."""
        summary = self.pe.quintiles[0].returns

        self.assertAlmostEqual(summary.cumulative_gross, 0.32)
        self.assertAlmostEqual(summary.cumulative_net, 0.31604264)
        self.assertAlmostEqual(summary.annualised_gross, 1.32**0.5 - 1)

    def test_spread_and_positive_years(self):
        """Quintile 5 is E (+50%) then A (0%): spreads 0.10 - 0.50 and 0.20 - 0.0."""
        first, second = self.pe.periods

        self.assertAlmostEqual(first.spread_gross, -0.40)
        self.assertAlmostEqual(second.spread_gross, 0.20)
        self.assertEqual(self.pe.positive_spread_years, 1)
        self.assertEqual(self.pe.full_years, 2)

    def test_benchmark_turnover_after_drift(self):
        """After year 1, A..E weigh (1.1, 1.2, 1.0, 0.9, 1.5) / 5.7; then all six get 1/6.

        In 34.2nds: A 0.9, B 1.5, C 0.3, D 0.3, E 3.3 and F 5.7 (bought new): 12 / 34.2.
        """
        first, second = (period.benchmark for period in self.pe.periods)

        self.assertAlmostEqual(first.gross_return, 0.14)
        self.assertAlmostEqual(second.gross_return, 0.4 / 6)
        self.assertAlmostEqual(second.turnover, 12 / 34.2)

    def test_median_market_value_of_known_values_only(self):
        """Quintile 1 of year 2 holds F (100) and B (300); year 1 quintile 3 has none."""
        first, second = self.pe.periods

        self.assertEqual(second.quintiles[0].median_market_value, Decimal(200))
        self.assertIsNone(first.quintiles[2].median_market_value)

    def test_catalogue_benchmark_holds_every_company(self):
        """F counts in the catalogue even in the year it has no P/E: 0.75 / 6 in year 1."""
        first, second = self.result.catalogue

        self.assertEqual(len(first.tickers), 6)
        self.assertAlmostEqual(first.gross_return, 0.75 / 6)
        self.assertAlmostEqual(second.gross_return, 0.4 / 6)


class IdentityTests(unittest.TestCase):
    def test_size_weighted_quintiles_equal_the_benchmark(self):
        """Quintiles split the ranked companies, so their weighted mean is the benchmark.

        If they did not, the spread would measure a different set of companies than the
        benchmark it is shown beside.
        """
        rng = random.Random(7)
        tickers = [f"T{number:03d}" for number in range(37)]
        candidates = []
        returns = {}
        for ticker in tickers:
            candidates.append(candidate(ticker, {"roe": rng.uniform(-0.2, 0.4)}))
            returns[ticker] = rng.uniform(-0.5, 1.0)
        period = HoldingPeriod(
            ranking_date=date(2020, 3, 31),
            buy_session=date(2020, 4, 1),
            sell_session=date(2021, 4, 1),
            candidates=candidates,
            returns=returns,
        )

        result = run_backtest([period], financial_tickers=set(), ratios=["roe"])

        ratio_period = result.ratios[0].periods[0]
        weighted = 0.0
        for quintile in ratio_period.quintiles:
            size = len(quintile.portfolio.tickers)
            weighted += quintile.portfolio.gross_return * size
        self.assertAlmostEqual(weighted / 37, ratio_period.benchmark.gross_return)


class RankingTests(unittest.TestCase):
    def test_ties_keep_ticker_order_in_both_directions(self):
        """As the screener sorts: BBB before CCC whether low or high values come first."""
        candidates = [
            candidate("CCC", {"pe": 10, "roe": 0.2}),
            candidate("AAA", {"pe": 5, "roe": 0.3}),
            candidate("BBB", {"pe": 10, "roe": 0.2}),
        ]

        by_pe, _ = ranking(candidates, "pe", set())
        by_roe, _ = ranking(candidates, "roe", set())

        self.assertEqual([item.ticker for item in by_pe], ["AAA", "BBB", "CCC"])
        self.assertEqual([item.ticker for item in by_roe], ["AAA", "BBB", "CCC"])

    def test_financial_firms_leave_only_their_ratios(self):
        """A bank stays in P/B and ROE but leaves ROA, equity/assets and both cash ratios."""
        values = {ratio: 1 for ratio in RATIO_LOWEST_FIRST}
        bank = candidate("JPM", values)

        for ratio in RATIO_LOWEST_FIRST:
            with self.subTest(ratio=ratio):
                ranked, coverage = ranking([bank], ratio, {"JPM"})
                excluded = ratio in FINANCIAL_EXCLUDED_RATIOS
                self.assertEqual(coverage.financial, 1 if excluded else 0)
                self.assertEqual(len(ranked), 0 if excluded else 1)

    def test_each_mark_leaves_out_exactly_the_ratios_it_covers(self):
        """The scopes METHODOLOGY gives for Spin-off, Share issue and Discontinued ops."""
        expected = {
            "spin_off": {"pe", "pb", "ps"},
            "share_issue": {"pb", "ps"},
            "discontinued_operations": {
                "pe",
                "roe",
                "roa",
                "net_margin",
                "ocf_to_net_income",
                "eps_growth",
            },
            "previous_discontinued_operations": {"eps_growth"},
        }
        for mark, ratios in expected.items():
            with self.subTest(mark=mark):
                marked = candidate("X", **{mark: True})
                self.assertEqual(flagged_ratios(marked), ratios)

    def test_coverage_counts_each_reason_once(self):
        """A financial firm without a value counts as financial; an empty mark as empty."""
        candidates = [
            candidate("BANK"),
            candidate("EMPTY", discontinued_operations=True),
            candidate("MARKED", {"roa": 0.1}, discontinued_operations=True),
            candidate("KEPT", {"roa": 0.1}),
        ]

        ranked, coverage = ranking(candidates, "roa", {"BANK"})

        self.assertEqual([item.ticker for item in ranked], ["KEPT"])
        self.assertEqual(
            (coverage.ranked, coverage.financial, coverage.empty, coverage.flagged),
            (1, 1, 1, 1),
        )


class QuintileSizeTests(unittest.TestCase):
    def test_first_quintiles_take_the_remainder(self):
        """Sizes measured on the real coverage (plan, section 2)."""
        self.assertEqual(quintile_sizes(171), [35, 34, 34, 34, 34])
        self.assertEqual(quintile_sizes(149), [30, 30, 30, 30, 29])
        self.assertEqual(quintile_sizes(5), [1, 1, 1, 1, 1])

    def test_fewer_than_five_companies_stop_the_backtest(self):
        with self.assertRaises(ValueError):
            quintile_sizes(4)


class TurnoverTests(unittest.TestCase):
    def test_unchanged_equal_holding_trades_nothing(self):
        self.assertAlmostEqual(turnover({"A": 0.5, "B": 0.5}, ["A", "B"]), 0.0)

    def test_full_replacement_sells_and_buys_everything(self):
        self.assertAlmostEqual(turnover({"A": 0.5, "B": 0.5}, ["C", "D"]), 2.0)


class RandomPortfolioTests(unittest.TestCase):
    def test_partial_fisher_yates_picks_by_hand(self):
        """random() = 0 keeps the order; random() near 1 swaps in the last remaining.

        Pool [1, 2, 3, 4], size 2, random() = 0.999: position 0 takes index 3 -> [4, 2, 3, 1];
        position 1 takes 1 + int(0.999 * 3) = 3 -> [4, 1, 3, 2]; mean of 4 and 1 is 2.5.
        """

        class FixedRandom:
            def __init__(self, value):
                self.value = value

            def random(self):
                return self.value

        pool = [1.0, 2.0, 3.0, 4.0]

        lowest = random_mean_returns(pool, 2, FixedRandom(0.0), count=1)
        highest = random_mean_returns(pool, 2, FixedRandom(0.999), count=1)

        self.assertEqual(lowest, [1.5])
        self.assertEqual(highest, [2.5])

    def test_draws_repeat_for_one_seed(self):
        """The same seed must give the same 1000 portfolios on every run and Python version.

        The digest was recorded on Python 3.13; a change means the draws, and so the p-values
        of an earlier result, are no longer reproducible (plan, D7).
        """
        pool = [number / 100 for number in range(-20, 40)]
        seed = draw_seed("pe", "2017-03-31", 1)

        means = random_mean_returns(pool, 12, random.Random(seed))

        digest = hashlib.sha256(repr(means).encode("utf-8")).hexdigest()
        self.assertEqual(digest, EXPECTED_DRAW_DIGEST)

    def test_seed_depends_on_ratio_year_and_quintile(self):
        seeds = {
            draw_seed("pe", "2017-03-31", 1),
            draw_seed("pb", "2017-03-31", 1),
            draw_seed("pe", "2018-03-31", 1),
            draw_seed("pe", "2017-03-31", 2),
        }
        self.assertEqual(len(seeds), 4)


EXPECTED_DRAW_DIGEST = (
    "faaa1f10bf7414e65f0ecff6cd8adaa3691ad002e66be780f6f8b69150518c73"
)


class StatisticsTests(unittest.TestCase):
    def test_stouffer_of_neutral_years_is_neutral(self):
        self.assertAlmostEqual(stouffer([0.5, 0.5, 0.5]), 0.5)

    def test_stouffer_strengthens_agreeing_years(self):
        """Two years at p = 0.025 (z = 1.96 each): Z = 2 * 1.96 / sqrt(2) = 2.77.

        A normal table gives 1 - Phi(2.77) = 0.0028.
        """
        self.assertAlmostEqual(stouffer([0.025, 0.025]), 0.0028, places=4)


class PeriodCheckTests(unittest.TestCase):
    def test_holdings_must_follow_each_other(self):
        """A gap between a sale and the next purchase would leave money out of the market."""
        first, second = known_example()
        moved = HoldingPeriod(
            ranking_date=second.ranking_date,
            buy_session=date(2018, 4, 3),
            sell_session=second.sell_session,
            candidates=second.candidates,
            returns=second.returns,
        )

        with self.assertRaises(ValueError):
            run_backtest([first, moved], financial_tickers=set(), ratios=["pe"])

    def test_missing_return_stops_the_backtest(self):
        """A company without a price must not drop silently out of a quintile."""
        first, _ = known_example()
        returns = dict(first.returns)
        del returns["C"]
        broken = HoldingPeriod(
            ranking_date=first.ranking_date,
            buy_session=first.buy_session,
            sell_session=first.sell_session,
            candidates=first.candidates,
            returns=returns,
        )

        with self.assertRaises(ValueError):
            run_backtest([broken], financial_tickers=set(), ratios=["pe"])

    def test_partial_period_stays_out_of_the_summary(self):
        """April to December 2025 is shown alone, not compounded with the full years."""
        first, second = known_example()
        partial = HoldingPeriod(
            ranking_date=second.ranking_date,
            buy_session=second.buy_session,
            sell_session=date(2018, 12, 31),
            candidates=second.candidates,
            returns=second.returns,
            partial=True,
        )

        result = run_backtest([first, partial], financial_tickers=set(), ratios=["pe"])

        pe = result.ratios[0]
        self.assertEqual(pe.full_years, 1)
        self.assertAlmostEqual(pe.quintiles[0].returns.cumulative_gross, 0.10)
        self.assertTrue(pe.periods[1].partial)


def with_month_paths(periods):
    """The example with two checkpoints a year: each company halfway at 1 + r/2, then 1 + r."""
    result = []
    for period in periods:
        growth = {}
        for ticker, value in period.returns.items():
            growth[ticker] = (1 + value / 2, 1 + value)
        midway = date(period.buy_session.year, 10, 31)
        checkpoints = (midway, period.sell_session)
        result.append(replace(period, checkpoints=checkpoints, growth=growth))
    return result


class SpreadTestTests(unittest.TestCase):
    def test_month_checkpoints_give_twelve_months_without_a_stub(self):
        """Holding 3 Apr 2017 - 2 Apr 2018 on weekdays: month ends April to February, then
        the sale; 29 Mar 2018 is dropped, because 2 April is only four days after it."""
        sessions = []
        day = date(2017, 4, 3)
        while day <= date(2018, 4, 2):
            if day.weekday() < 5:
                sessions.append(day)
            day += timedelta(days=1)

        checkpoints = month_checkpoints(sessions, date(2017, 4, 3), date(2018, 4, 2))

        self.assertEqual(len(checkpoints), 12)
        self.assertEqual(checkpoints[0], date(2017, 4, 28))
        self.assertEqual(checkpoints[-2], date(2018, 2, 28))
        self.assertEqual(checkpoints[-1], date(2018, 4, 2))

    def test_monthly_returns_follow_the_drifting_portfolio(self):
        """A at 1.1 then 1.21, B at 0.9 then 0.81: the portfolio is worth 1.0 then 1.01."""
        period = replace(
            known_example()[0],
            checkpoints=(date(2017, 10, 31), date(2018, 4, 2)),
            growth={"A": (1.1, 1.21), "B": (0.9, 0.81)},
        )

        returns = monthly_returns(["A", "B"], period)

        self.assertAlmostEqual(returns[0], 0.0)
        self.assertAlmostEqual(returns[1], 0.01)

    def test_t_statistic_of_known_values(self):
        """1, 2, 3: mean 2, standard deviation 1, standard error 1/sqrt(3), so t = 2 sqrt(3)."""
        self.assertAlmostEqual(t_statistic([1, 2, 3]), 2 * 3**0.5)
        self.assertIsNone(t_statistic([0.1]))
        self.assertIsNone(t_statistic([0.1, 0.1]))

    def test_spread_test_of_the_known_example(self):
        """Year 1: A (Q1) 1.05, 1.10 and E (Q5) 1.25, 1.50 give spreads -0.2 and
        1.10/1.05 - 1.5/1.25 = -0.152381. Year 2: F and B (Q1) average 1.10, 1.20 and A (Q5)
        stays at 1: spreads 0.1 and 0.090909. Mean -0.040368, x 12 = -0.484416; deviations
        -0.159632, -0.112013, 0.140368, 0.131277 give a standard deviation of 0.158078,
        a standard error of 0.079039 and t = -0.5107."""
        periods = with_month_paths(known_example())

        pe = run_backtest(periods, financial_tickers=set(), ratios=["pe"]).ratios[0]

        self.assertEqual(pe.spread_months, 4)
        self.assertAlmostEqual(pe.spread_mean, -0.484416, places=6)
        self.assertAlmostEqual(pe.spread_t, -0.5107, places=4)

    def test_spread_test_needs_month_ends_of_every_company(self):
        """A company without a month-end close must stop the run, not drop out silently."""
        first, second = with_month_paths(known_example())
        growth = dict(first.growth)
        del growth["C"]

        with self.assertRaises(ValueError):
            run_backtest(
                [replace(first, growth=growth), second],
                financial_tickers=set(),
                ratios=["pe"],
            )


def with_daily_paths(periods, chosen_paths):
    """Three sessions a year: the chosen companies follow `chosen_paths[year][ticker]`, every
    other company rises in equal steps to its return."""
    result = []
    for period, chosen in zip(periods, chosen_paths, strict=True):
        sessions = (
            date(period.buy_session.year, 8, 1),
            date(period.buy_session.year, 12, 1),
            period.sell_session,
        )
        daily_growth = {}
        for ticker, value in period.returns.items():
            daily_growth[ticker] = chosen.get(
                ticker, (1 + value / 3, 1 + 2 * value / 3, 1 + value)
            )
        result.append(replace(period, sessions=sessions, daily_growth=daily_growth))
    return result


class RiskTests(unittest.TestCase):
    def test_volatility_of_the_monthly_returns_of_the_known_example(self):
        """Quintile 1 has the monthly returns 0.05, 1.10 / 1.05 - 1 = 0.047619 (A), then
        0.10 and 1.20 / 1.10 - 1 = 0.090909 (F and B). Mean 0.072132, deviations -0.022132,
        -0.024513, 0.027868, 0.018777; squares summing to 0.0022199, over 3 is 0.00073998,
        so the standard deviation is 0.027202 and per year 0.027202 * sqrt(12) = 0.094232."""
        periods = with_month_paths(known_example())

        pe = run_backtest(periods, financial_tickers=set(), ratios=["pe"]).ratios[0]

        self.assertAlmostEqual(pe.quintiles[0].returns.volatility, 0.094232, places=6)

    def test_worst_drop_crosses_the_purchase_between_years(self):
        """Quintile 1 holds A in year 1 (1.2, 0.9, 1.1) and F and B in year 2, both at 0.8
        on the first session. Year 1 alone falls 25% (1.2 -> 0.9), year 2 alone 20%; chained,
        year 2 starts from 1.1 and falls to 1.1 * 0.8 = 0.88 while the peak is still 1.2,
        so the worst drop is 0.88 / 1.2 - 1 = -0.266667."""
        periods = with_daily_paths(
            known_example(),
            [
                {"A": (1.2, 0.9, 1.1)},
                {"F": (0.8, 0.9, 1.3), "B": (0.8, 1.0, 1.1)},
            ],
        )

        pe = run_backtest(periods, financial_tickers=set(), ratios=["pe"]).ratios[0]

        self.assertAlmostEqual(pe.quintiles[0].returns.worst_drop, -0.266667, places=6)

    def test_rising_portfolio_has_no_drop(self):
        """Quintile 5 holds E (+50%) then A (0%), both rising or flat in equal steps."""
        periods = with_daily_paths(known_example(), [{}, {}])

        pe = run_backtest(periods, financial_tickers=set(), ratios=["pe"]).ratios[0]

        self.assertEqual(pe.quintiles[4].returns.worst_drop, 0.0)

    def test_risk_is_empty_without_month_ends_or_daily_closes(self):
        """The known example has neither, so neither measure is made up."""
        result = run_backtest(known_example(), financial_tickers=set(), ratios=["pe"])

        summary = result.ratios[0].quintiles[0].returns
        self.assertIsNone(summary.volatility)
        self.assertIsNone(summary.worst_drop)
        self.assertIsNone(result.catalogue_summary.worst_drop)

    def test_worst_drop_needs_daily_closes_of_every_company(self):
        """A company without a daily close must stop the run, not drop out silently."""
        first, second = with_daily_paths(known_example(), [{}, {}])
        daily_growth = dict(first.daily_growth)
        del daily_growth["C"]

        with self.assertRaises(ValueError):
            run_backtest(
                [replace(first, daily_growth=daily_growth), second],
                financial_tickers=set(),
                ratios=["pe"],
            )


if __name__ == "__main__":
    unittest.main()
