export interface Stock {
  ticker: string;
  name: string;
  exchange: string;
  sector: string | null;
  financial_history_note?: string | null;
}

export interface Price {
  date: string;
  open: string;
  high: string;
  low: string;
  close: string;
  adjusted_close: string;
  volume: number;
}

export type FinancialStatus = 'ready' | 'not_available' | 'needs_review';

export interface FilingValue {
  source_cik?: string | null;
  taxonomy: string;
  tag: string;
  unit: string;
  value: string;
  period_start: string;
  period_end: string;
  filed: string;
  accession: string;
  form: string;
  statement_url?: string | null;
  statement_label?: string | null;
  statement_column?: string | null;
}

export interface EpsShareBasis {
  as_of: string;
  reported_value: string;
  new_shares: number;
  old_shares: number;
  applied_splits: {
    date: string;
    new_shares: number;
    old_shares: number;
    source_url: string;
  }[];
}

/** The annual report growth was read from, with both years as it states them. */
export interface GrowthReport {
  accession: string;
  filed: string;
  source_cik: string;
  earlier_period_end: string;
  earlier_value: string;
  later_value: string;
}

export interface AnnualMetric {
  status: FinancialStatus;
  value: string | null;
  variants: FilingValue[];
  selected_tag?: string | null;
  selected_sources?: FilingValue[];
  calculation?: string | null;
  selection_note?: string | null;
  eps_basis?: 'basic' | 'diluted' | null;
  eps_share_basis?: EpsShareBasis | null;
  scope?: 'total' | 'continuing' | null;
  // Change from the year before, both years from one annual report (0.05 is 5%).
  growth?: string | null;
  growth_report?: GrowthReport | null;
  // The table's two values come from different definitions; the change above does not.
  series_break?: boolean;
}

export interface AnnualPeriod {
  fiscal_year?: number | null;
  period_note?: string | null;
  period_start: string;
  period_end: string;
  metrics: Record<string, AnnualMetric>;
}
