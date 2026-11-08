// Mirrors backend/app/schemas/screener.py. Values are decimal strings; null means empty, never zero.

export interface Ratios {
  pe: string | null;
  pb: string | null;
  ps: string | null;
  roe: string | null;
  roa: string | null;
  net_margin: string | null;
  operating_margin: string | null;
  revenue_growth: string | null;
  eps_growth: string | null;
  ocf_to_net_income: string | null;
  fcf_margin: string | null;
  equity_to_assets: string | null;
}

export type RatioKey = keyof Ratios;

export interface PeriodRatios {
  period_start: string;
  period_end: string;
  price_date: string | null;
  price: string | null;
  eps_basis: 'basic' | 'diluted' | null;
  ocf_scope: 'total' | 'continuing' | null;
  shares_basis: 'reported' | 'calculated' | null;
  revenue_basis_break: boolean;
  eps_basis_break: boolean;
  ratios: Ratios;
}

export interface ScreenerRow {
  ticker: string;
  name: string;
  sector: string | null;
  // Null only when the company has no annual report known on the valuation date.
  latest: PeriodRatios | null;
}

export interface ScreenerResponse {
  valuation_date: string;
  rows: ScreenerRow[];
}
