// A small backtest answer for the page's tests: two full years and the partial one.
import {
  BacktestResponse,
  CataloguePeriod,
  QuintilePeriod,
  RatioBacktest,
  RatioPeriod,
} from '../../models/backtest';
import { RatioKey } from '../../models/screener';

const HOLDINGS = [
  { ranking_date: '2017-03-31', buy_session: '2017-04-03', sell_session: '2018-04-02' },
  { ranking_date: '2018-03-31', buy_session: '2018-04-02', sell_session: '2019-04-01' },
  { ranking_date: '2025-03-31', buy_session: '2025-04-01', sell_session: '2025-12-31' },
];

function quintile(number: number, gross: number, withMembers: boolean): QuintilePeriod {
  return {
    number,
    count: 2,
    gross_return: gross,
    // A visible cost, so that the tests can tell the two views apart.
    net_return: gross - 0.01,
    turnover: 1,
    median_market_value: '63000000000',
    p_value: 0.25,
    members: withMembers
      ? [
          { ticker: `A${number}`, value: '12.5', period_return: gross, market_value: '1' },
          { ticker: `B${number}`, value: '13.5', period_return: gross, market_value: '2' },
        ]
      : null,
  };
}

function period(index: number, withMembers: boolean): RatioPeriod {
  const quintiles = [0.2, 0.15, 0.1, 0.05, 0.0].map((gross, position) =>
    quintile(position + 1, gross, withMembers),
  );
  return {
    ...HOLDINGS[index],
    partial: index === HOLDINGS.length - 1,
    coverage: { ranked: 10, financial: 0, empty: 2, flagged: 1 },
    quintiles,
    benchmark: { count: 10, gross_return: 0.1, net_return: 0.09, turnover: 1 },
    spread_gross: 0.2,
    spread_net: 0.2,
  };
}

export function ratioBacktest(ratio: RatioKey, withMembers = false): RatioBacktest {
  return {
    ratio,
    lowest_first: ratio === 'pe' || ratio === 'pb' || ratio === 'ps',
    excludes_financial: ratio === 'roa',
    full_years: 2,
    positive_spread_years: 2,
    coin_probability: 0.25,
    spread_annualised_gross: 0.2,
    spread_annualised_net: 0.2,
    quintiles: [1, 2, 3, 4, 5].map((number) => ({
      number,
      cumulative_gross: 0.44,
      cumulative_net: 0.42,
      annualised_gross: 0.2,
      annualised_net: 0.19,
      combined_p_value: number === 1 ? 0.0004 : 0.5,
    })),
    benchmark: {
      cumulative_gross: 0.21,
      cumulative_net: 0.19,
      annualised_gross: 0.1,
      annualised_net: 0.09,
    },
    periods: HOLDINGS.map((_, index) => period(index, withMembers)),
  };
}

function cataloguePeriod(index: number): CataloguePeriod {
  return {
    ...HOLDINGS[index],
    partial: index === HOLDINGS.length - 1,
    count: 200,
    gross_return: 0.18,
    net_return: 0.17,
    turnover: 1,
  };
}

export function backtestResponse(ratios: RatioBacktest[]): BacktestResponse {
  return {
    trade_cost: 0.001,
    random_portfolios: 1000,
    seed: 20170331,
    catalogue: HOLDINGS.map((_, index) => cataloguePeriod(index)),
    catalogue_summary: {
      cumulative_gross: 0.39,
      cumulative_net: 0.37,
      annualised_gross: 0.18,
      annualised_net: 0.17,
    },
    ratios,
  };
}
