// Small screener rows for the tests of the screener modules. Only the values a test names
// are set; every other ratio is empty.
import { PeriodRatios, Ratios, ScreenerRow } from '../../models/screener';

export const EMPTY_RATIOS: Ratios = {
  pe: null,
  pb: null,
  ps: null,
  roe: null,
  roa: null,
  net_margin: null,
  operating_margin: null,
  revenue_growth: null,
  eps_growth: null,
  ocf_to_net_income: null,
  fcf_margin: null,
  equity_to_assets: null,
};

export function period(
  overrides: Partial<PeriodRatios> = {},
  ratios: Partial<Ratios> = {},
): PeriodRatios {
  return {
    period_start: '2024-01-01',
    period_end: '2024-12-31',
    price_date: '2025-12-31',
    price: '100',
    eps_basis: 'diluted',
    ocf_scope: 'total',
    shares_basis: 'reported',
    revenue_basis_break: false,
    eps_basis_break: false,
    ...overrides,
    ratios: { ...EMPTY_RATIOS, ...ratios },
  };
}

/** A company named "<TICKER> Inc." with the given ratios. */
export function row(
  ticker: string,
  ratios: Partial<Ratios> = {},
  sector: string | null = 'Energy',
): ScreenerRow {
  return {
    ticker,
    name: `${ticker} Inc.`,
    sector,
    latest: period({}, ratios),
  };
}

export function tickers(rows: ScreenerRow[]): string[] {
  return rows.map((item) => item.ticker);
}
