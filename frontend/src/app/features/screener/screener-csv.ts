// CSV export of the screener: the values as the API returns them, so that the file is fit for
// further analysis.
import { ScreenerRow, SpinOff } from '../../models/screener';
import { columnValue } from './screener-columns';
import { CRITERION_COLUMNS } from './screener-filters';

interface CsvColumn {
  header: string;
  value: (row: ScreenerRow) => string | boolean | null;
}

/** One part of every spin-off in one field, separated by semicolons; empty when none. */
function spinOffField(row: ScreenerRow, part: (spinOff: SpinOff) => string): string | null {
  if (row.latest === null) {
    return null;
  }
  const parts = row.latest.spin_offs.map(part);
  return parts.join(';');
}

const CSV_COLUMNS: CsvColumn[] = [
  { header: 'ticker', value: (row) => row.ticker },
  { header: 'name', value: (row) => row.name },
  { header: 'sector', value: (row) => row.sector },
  { header: 'price_date', value: (row) => row.latest?.price_date ?? null },
  { header: 'price', value: (row) => row.latest?.price ?? null },
  { header: 'period_start', value: (row) => row.latest?.period_start ?? null },
  { header: 'period_end', value: (row) => row.latest?.period_end ?? null },
  ...CRITERION_COLUMNS.map((column) => ({
    header: column.key,
    value: (row: ScreenerRow) => columnValue(row, column.key),
  })),
  { header: 'eps_basis', value: (row) => row.latest?.eps_basis ?? null },
  { header: 'shares_basis', value: (row) => row.latest?.shares_basis ?? null },
  { header: 'ocf_scope', value: (row) => row.latest?.ocf_scope ?? null },
  { header: 'revenue_series_break', value: (row) => row.latest?.revenue_basis_break ?? null },
  { header: 'eps_series_break', value: (row) => row.latest?.eps_basis_break ?? null },
  {
    header: 'spin_off_dates',
    value: (row) => spinOffField(row, (spinOff) => spinOff.event_date),
  },
  {
    header: 'spin_off_fractions',
    value: (row) => spinOffField(row, (spinOff) => spinOff.separated_fraction),
  },
  { header: 'exchangeable_units', value: (row) => row.latest?.exchangeable_units ?? null },
];

/** One CSV field: quoted when it holds a separator, a quote or a line break (RFC 4180). */
function csvField(value: string | boolean | null): string {
  if (value === null) {
    return '';
  }
  const text = String(value);
  const needsQuotes = /[",\r\n]/.test(text);
  if (!needsQuotes) {
    return text;
  }
  return `"${text.replaceAll('"', '""')}"`;
}

/**
 * The rows as CSV, in the given order. Ratios stay plain fractions (0.15 is 15%) and an empty
 * value stays an empty field, never zero.
 */
export function screenerCsv(rows: ScreenerRow[], valuationDate: string): string {
  const header = ['valuation_date', ...CSV_COLUMNS.map((column) => column.header)];
  const lines = [header.join(',')];
  for (const row of rows) {
    const fields = CSV_COLUMNS.map((column) => csvField(column.value(row)));
    lines.push([valuationDate, ...fields].join(','));
  }
  return lines.join('\r\n') + '\r\n';
}
