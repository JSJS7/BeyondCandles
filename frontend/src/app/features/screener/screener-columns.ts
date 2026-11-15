// The columns of the screener table: what each one holds and how its values are shown.
import { PeriodRatios, RatioKey, ScreenerRow } from '../../models/screener';
import { formatDate } from '../../shared/date-format';
import { formatFixed } from '../../shared/decimal-format';
import { formatRatio, RATIO_DEFINITIONS, RatioKind } from '../../shared/ratio-definitions';
import { RATIO_CELL_LABELS, RatioCellLabel, ratioLabels } from '../../shared/ratio-labels';

type ColumnKind = 'text' | 'date' | 'price' | RatioKind;
export type ColumnKey = 'sector' | 'price' | 'period_end' | RatioKey;

export interface ScreenerColumn {
  key: ColumnKey;
  label: string;
  kind: ColumnKind;
  // A shorter table header (see RatioDefinition).
  shortLabel?: string;
}

export const SCREENER_COLUMNS: ScreenerColumn[] = [
  { key: 'sector', label: 'Sector', kind: 'text' },
  { key: 'price', label: 'Price', kind: 'price' },
  { key: 'period_end', label: 'Period end', kind: 'date' },
  ...RATIO_DEFINITIONS,
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
    case 'percent':
      return formatRatio(column.kind, value);
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
export const SCREENER_CELL_LABELS = RATIO_CELL_LABELS;

/** Short labels that change how a value may be read; the same words as the financials table. */
export function cellLabels(latest: PeriodRatios | null, key: ColumnKey): RatioCellLabel[] {
  const isRatio = key !== 'sector' && key !== 'price' && key !== 'period_end';
  if (latest === null || !isRatio) {
    return [];
  }
  return ratioLabels(latest, key);
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
