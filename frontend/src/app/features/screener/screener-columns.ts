// The columns of the screener table: what each one holds and how its values are shown.
import { PeriodRatios, RatioKey, ScreenerRow } from '../../models/screener';
import { formatDate } from '../../shared/date-format';
import { formatFixed, formatPercent } from '../../shared/decimal-format';

type ColumnKind = 'text' | 'date' | 'price' | 'multiple' | 'percent';
export type ColumnKey = 'sector' | 'price' | 'period_end' | RatioKey;

export interface ScreenerColumn {
  key: ColumnKey;
  label: string;
  kind: ColumnKind;
  // A shorter table header, so that the table fits a laptop screen; `label` stays the name
  // everywhere else and is what assistive technology reads.
  shortLabel?: string;
}

export const SCREENER_COLUMNS: ScreenerColumn[] = [
  { key: 'sector', label: 'Sector', kind: 'text' },
  { key: 'price', label: 'Price', kind: 'price' },
  { key: 'period_end', label: 'Period end', kind: 'date' },
  { key: 'pe', label: 'P/E', kind: 'multiple' },
  { key: 'pb', label: 'P/B', kind: 'multiple' },
  { key: 'ps', label: 'P/S', kind: 'multiple' },
  { key: 'roe', label: 'ROE', kind: 'percent' },
  { key: 'roa', label: 'ROA', kind: 'percent' },
  { key: 'net_margin', label: 'Net margin', kind: 'percent' },
  { key: 'operating_margin', label: 'Operating margin', kind: 'percent', shortLabel: 'Op. margin' },
  { key: 'fcf_margin', label: 'FCF margin', kind: 'percent' },
  { key: 'revenue_growth', label: 'Revenue growth', kind: 'percent', shortLabel: 'Rev. growth' },
  { key: 'eps_growth', label: 'EPS growth', kind: 'percent' },
  { key: 'ocf_to_net_income', label: 'OCF / net income', kind: 'multiple' },
  { key: 'equity_to_assets', label: 'Equity / assets', kind: 'percent' },
];

/** The column with this key, or undefined for any other text (e.g. from the address). */
export function findColumn(key: string): ScreenerColumn | undefined {
  return SCREENER_COLUMNS.find((column) => column.key === key);
}

/** The raw value of one cell as the API returns it; null when the company has none. */
export function columnValue(row: ScreenerRow, key: ColumnKey): string | null {
  if (key === 'sector') {
    return row.sector;
  }
  if (row.latest === null) {
    return null;
  }
  if (key === 'price') {
    return row.latest.price;
  }
  if (key === 'period_end') {
    return row.latest.period_end;
  }
  return row.latest.ratios[key];
}

export function formatMultiple(value: string): string {
  return formatFixed(value, 2);
}

/** One value of a column as the table shows it. */
export function formatValue(column: ScreenerColumn, value: string): string {
  switch (column.kind) {
    case 'text':
      return value;
    case 'date':
      return formatDate(value);
    case 'price':
      // In dollars, as the price on the company page; prices are never negative.
      return `$${formatFixed(value, 2)}`;
    case 'multiple':
      return formatMultiple(value);
    case 'percent':
      return formatPercent(value);
  }
}

/** The text of one cell, or null when it has no value and must show a dash, never zero. */
export function cellText(row: ScreenerRow, column: ScreenerColumn): string | null {
  const value = columnValue(row, column.key);
  if (value === null) {
    return null;
  }
  return formatValue(column, value);
}

/** Every label a screener cell can carry; the page's reading guide explains each one (K15). */
export const SCREENER_CELL_LABELS = [
  'Basic',
  'Calculated',
  'Series break',
  'Continuing ops',
  'Spin-off',
] as const;
export type ScreenerCellLabel = (typeof SCREENER_CELL_LABELS)[number];

/** Short labels that change how a value may be read; the same words as the financials table. */
export function cellLabels(latest: PeriodRatios | null, key: ColumnKey): ScreenerCellLabel[] {
  if (latest === null) {
    return [];
  }

  // A break can also be the reason why growth is empty, so it does not depend on the value.
  if (key === 'revenue_growth') {
    return latest.revenue_basis_break ? ['Series break'] : [];
  }
  if (key === 'eps_growth') {
    const labels: ScreenerCellLabel[] = latest.eps_basis_break ? ['Series break'] : [];
    if (latest.ratios.eps_growth !== null && latest.eps_basis === 'basic') {
      labels.push('Basic');
    }
    return labels;
  }

  const hasValue = key !== 'sector' && key !== 'price' && key !== 'period_end';
  if (!hasValue || latest.ratios[key] === null) {
    return [];
  }
  const labels: ScreenerCellLabel[] = [];
  if (key === 'pe' && latest.eps_basis === 'basic') {
    labels.push('Basic');
  }
  const usesShareCount = key === 'pb' || key === 'ps';
  if (usesShareCount && latest.shares_basis === 'calculated') {
    labels.push('Calculated');
  }
  if (key === 'fcf_margin' && latest.ocf_scope === 'continuing') {
    labels.push('Continuing ops');
  }
  const usesPrice = key === 'pe' || key === 'pb' || key === 'ps';
  if (usesPrice && latest.spin_offs.length > 0) {
    labels.push('Spin-off');
  }
  return labels;
}

/** The columns to show, in table order, without the hidden ones. */
export function visibleColumns(hidden: ColumnKey[]): ScreenerColumn[] {
  return SCREENER_COLUMNS.filter((column) => !hidden.includes(column.key));
}

/** The short text of the column control, e.g. "Columns" or "Columns (12 of 15)". */
export function columnSummary(hidden: ColumnKey[]): string {
  if (hidden.length === 0) {
    return 'Columns';
  }
  const shown = SCREENER_COLUMNS.length - hidden.length;
  return `Columns (${shown} of ${SCREENER_COLUMNS.length})`;
}
