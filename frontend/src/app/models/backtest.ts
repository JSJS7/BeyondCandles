// The annual quintile backtest as GET /backtest and GET /backtest/{ratio} return it (DEC-33).
// Returns, turnover and p-values are JSON numbers and plain fractions (0.25 is 25%); ratio
// values and market values are decimal text, as in the screener.
import { RatioKey } from './screener';

export interface BacktestCoverage {
  ranked: number;
  // SIC 6000-6999, only for FCF margin, OCF / net income, ROA and equity / assets.
  financial: number;
  empty: number;
  // Marked Spin-off, Share issue or Discontinued ops for this ratio.
  flagged: number;
}

export interface BacktestPortfolio {
  count: number;
  gross_return: number;
  net_return: number;
  turnover: number;
}

export interface QuintileMember {
  ticker: string;
  value: string;
  period_return: number;
  market_value: string | null;
}

export interface QuintilePeriod extends BacktestPortfolio {
  number: number;
  median_market_value: string | null;
  // Share of random portfolios of this size that did at least as well before costs.
  p_value: number;
  // Null in GET /backtest; listed in GET /backtest/{ratio}.
  members: QuintileMember[] | null;
}

export interface RatioPeriod {
  ranking_date: string;
  buy_session: string;
  sell_session: string;
  // April to December 2025: shown alone, outside the summaries.
  partial: boolean;
  coverage: BacktestCoverage;
  quintiles: QuintilePeriod[];
  benchmark: BacktestPortfolio;
  spread_gross: number;
  spread_net: number;
}

export interface ReturnSummary {
  cumulative_gross: number;
  cumulative_net: number;
  annualised_gross: number;
  annualised_net: number;
  // Before costs: monthly standard deviation × √12, and the largest fall of the daily value
  // from its peak as a negative fraction (-0.45 is 45%). Null without enough prices.
  volatility: number | null;
  worst_drop: number | null;
}

export interface QuintileSummary extends ReturnSummary {
  number: number;
  combined_p_value: number | null;
}

export interface RatioBacktest {
  ratio: RatioKey;
  lowest_first: boolean;
  excludes_financial: boolean;
  full_years: number;
  positive_spread_years: number;
  // The test of the ranking, before costs: monthly quintile 1 - 5 returns over the full years,
  // their mean per year (x 12) and t, the mean over its standard error; null without two months.
  spread_mean: number | null;
  spread_t: number | null;
  spread_months: number;
  quintiles: QuintileSummary[];
  benchmark: ReturnSummary;
  periods: RatioPeriod[];
}

export interface CataloguePeriod extends BacktestPortfolio {
  ranking_date: string;
  buy_session: string;
  sell_session: string;
  partial: boolean;
}

export interface BacktestResponse {
  trade_cost: number;
  random_portfolios: number;
  seed: number;
  catalogue: CataloguePeriod[];
  catalogue_summary: ReturnSummary;
  ratios: RatioBacktest[];
}
