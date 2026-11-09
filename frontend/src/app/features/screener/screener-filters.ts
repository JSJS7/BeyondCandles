// Which rows the screener shows: sectors, search and ranges of ratios. Rows are filtered in the
// browser, because every company is already loaded for the valuation date.
import { ScreenerRow } from '../../models/screener';
import { EXACT_TICKER, matchRank } from '../../shared/search-match';
import {
  ColumnKey,
  columnValue,
  findColumn,
  SCREENER_COLUMNS,
  ScreenerColumn,
} from './screener-columns';

export interface CriterionInput {
  key: ColumnKey;
  // The text as typed, so that an unfinished number such as "1." is never rewritten.
  min: string;
  max: string;
}

export interface CriterionStatus {
  min: number | null;
  max: number | null;
  minInvalid: boolean;
  maxInvalid: boolean;
  reversed: boolean;
  active: boolean;
}

export interface CriterionStats {
  count: number;
  p10: string;
  median: string;
  p90: string;
}

/** The columns that can be limited by a range: the ratios, not the text, price or date. */
export const CRITERION_COLUMNS: ScreenerColumn[] = SCREENER_COLUMNS.filter(
  (column) => column.kind === 'multiple' || column.kind === 'percent',
);

const NUMBER_TEXT = /^-?(\d+\.?\d*|\.\d+)$/;

// A decimal comma is accepted, because it is how many people type a decimal number.
function cleanNumberText(text: string): string {
  return text.trim().replace(',', '.');
}

/** True for empty text and for a plain decimal number; false for anything else. */
export function isNumberText(text: string): boolean {
  const cleaned = cleanNumberText(text);
  return cleaned === '' || NUMBER_TEXT.test(cleaned);
}

/** Percent typed by the user as a ratio, moving the point as text: "7.3" gives 0.073 exactly. */
function percentTextToRatio(text: string): number {
  const negative = text.startsWith('-');
  const unsigned = negative ? text.slice(1) : text;
  const [integer = '', fraction = ''] = unsigned.split('.');
  const padded = integer.padStart(2, '0');
  const wholePart = padded.slice(0, -2) || '0';
  const ratioText = `${wholePart}.${padded.slice(-2)}${fraction}`;
  return Number(`${negative ? '-' : ''}${ratioText}`);
}

/** One typed bound as a number comparable with the API value; null when empty or invalid. */
function boundValue(text: string, key: ColumnKey): number | null {
  const cleaned = cleanNumberText(text);
  if (cleaned === '' || !NUMBER_TEXT.test(cleaned)) {
    return null;
  }
  if (findColumn(key)?.kind === 'percent') {
    return percentTextToRatio(cleaned);
  }
  return Number(cleaned);
}

export function criterionStatus(criterion: CriterionInput): CriterionStatus {
  const min = boundValue(criterion.min, criterion.key);
  const max = boundValue(criterion.max, criterion.key);
  return {
    min,
    max,
    minInvalid: !isNumberText(criterion.min),
    maxInvalid: !isNumberText(criterion.max),
    reversed: min !== null && max !== null && min > max,
    active: min !== null || max !== null,
  };
}

/**
 * The unit written inside the input: percent for shares, "x" for multiples (5x). The times sign
 * is not used, because it looks like the button that removes the criterion.
 */
export function criterionUnit(key: ColumnKey): string {
  return findColumn(key)?.kind === 'percent' ? '%' : 'x';
}

/**
 * Rows inside every active range. A row with no value for an active criterion is left out,
 * because an empty value is not a value that can satisfy a range (never read as zero).
 */
export function filterRows(rows: ScreenerRow[], criteria: CriterionInput[]): ScreenerRow[] {
  const activeRanges = criteria
    .map((criterion) => ({ key: criterion.key, ...criterionStatus(criterion) }))
    .filter((range) => range.active);

  return rows.filter((row) =>
    activeRanges.every((range) => {
      const value = columnValue(row, range.key);
      if (value === null) {
        return false;
      }
      const number = Number(value);
      const aboveMinimum = range.min === null || number >= range.min;
      const belowMaximum = range.max === null || number <= range.max;
      return aboveMinimum && belowMaximum;
    }),
  );
}

/**
 * The nearest-rank percentile: the value at position ceil(p × n) of the sorted values. It is
 * always one company's real value, so it is shown in the same format as the table.
 */
function nearestRank<T>(sortedValues: T[], percentile: number): T {
  const position = Math.max(1, Math.ceil(percentile * sortedValues.length));
  return sortedValues[position - 1];
}

/**
 * Where the values of one ratio lie, so that a range can be chosen with sense: the median and
 * the middle 80% (10th to 90th percentile). Extremes such as a P/E of 300 would make a plain
 * minimum and maximum say little. Null when no company has a value.
 */
export function criterionStats(rows: ScreenerRow[], key: ColumnKey): CriterionStats | null {
  const values: { text: string; number: number }[] = [];
  for (const row of rows) {
    const text = columnValue(row, key);
    if (text !== null) {
      values.push({ text, number: Number(text) });
    }
  }
  if (values.length === 0) {
    return null;
  }

  values.sort((first, second) => first.number - second.number);
  return {
    count: values.length,
    p10: nearestRank(values, 0.1).text,
    median: nearestRank(values, 0.5).text,
    p90: nearestRank(values, 0.9).text,
  };
}

/** Rows of the chosen sectors, or of all others when the choice is an exclusion. */
export function filterBySector(
  rows: ScreenerRow[],
  sectors: string[],
  exclude: boolean,
): ScreenerRow[] {
  if (sectors.length === 0) {
    return rows;
  }
  const chosen = new Set(sectors);
  return rows.filter((row) => {
    const isChosen = row.sector !== null && chosen.has(row.sector);
    return exclude ? !isChosen : isChosen;
  });
}

/**
 * Rows whose ticker or name contains the text, ignoring case; empty text keeps every row.
 * The same companies as the top search finds (M35). The table keeps its own sort order.
 */
export function filterBySearch(rows: ScreenerRow[], text: string): ScreenerRow[] {
  const query = text.trim().toLowerCase();
  if (query === '') {
    return rows;
  }
  return rows.filter((row) => {
    const rank = matchRank(row.ticker, row.name, query);
    return rank !== null;
  });
}

/** The same rows with the company whose ticker is exactly the text moved to the top. */
export function exactTickerFirst(rows: ScreenerRow[], text: string): ScreenerRow[] {
  const query = text.trim().toLowerCase();
  if (query === '') {
    return rows;
  }
  const exact = rows.filter((row) => matchRank(row.ticker, row.name, query) === EXACT_TICKER);
  const others = rows.filter((row) => !exact.includes(row));
  return [...exact, ...others];
}

/** The short text of the sector control, e.g. "All sectors", "Energy" or "All except 2 sectors". */
export function sectorSummary(sectors: string[], exclude: boolean): string {
  if (sectors.length === 0) {
    return 'All sectors';
  }
  const named = sectors.length === 1 ? sectors[0] : `${sectors.length} sectors`;
  return exclude ? `All except ${named}` : named;
}
