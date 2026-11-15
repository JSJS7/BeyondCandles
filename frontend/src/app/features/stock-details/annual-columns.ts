// The measures of the annual table and how their values are shown, shared by the table and
// its sources panel.
import { AnnualMetric, AnnualPeriod } from '../../models/stock';
import { formatMillions, formatPerShare } from '../../shared/decimal-format';

export interface MetricColumn {
  key: string;
  label: string;
  unit: string;
  perShare: boolean;
}

export const ANNUAL_COLUMNS: MetricColumn[] = [
  { key: 'revenue', label: 'Revenue', unit: 'USD millions', perShare: false },
  { key: 'net_income', label: 'Net income', unit: 'USD millions', perShare: false },
  { key: 'eps_diluted', label: 'EPS', unit: 'USD per share', perShare: true },
  { key: 'equity', label: 'Parent equity', unit: 'USD millions, year end', perShare: false },
  {
    key: 'operating_cash_flow',
    label: 'Operating cash flow',
    unit: 'USD millions',
    perShare: false,
  },
];

// Also labels a row of annual ratios, which carries the same fiscal year.
export function fiscalYearLabel(period: Pick<AnnualPeriod, 'fiscal_year'>): string {
  if (period.fiscal_year != null) {
    return `FY ${period.fiscal_year}`;
  }
  return 'Fiscal year needs review';
}

/** The rounded value shown in the table. */
export function tableValue(column: MetricColumn, value: string): string {
  if (column.perShare) {
    return formatPerShare(value);
  }
  return formatMillions(value);
}

/** EPS restated for stock splits after its filing. */
export function isSplitAdjusted(cell: AnnualMetric): boolean {
  const appliedSplits = cell.eps_share_basis?.applied_splits.length ?? 0;
  return appliedSplits > 0;
}
