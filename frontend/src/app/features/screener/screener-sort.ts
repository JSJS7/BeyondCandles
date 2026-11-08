// The order of the screener rows.
import { ScreenerRow } from '../../models/screener';
import { ColumnKey, columnValue, findColumn } from './screener-columns';

// The company column has no value of its own: it is sorted by ticker and rendered as a link.
export type SortKey = 'company' | ColumnKey;
export type SortDirection = 'asc' | 'desc';

export interface ScreenerSort {
  key: SortKey;
  direction: SortDirection;
}

export const DEFAULT_SORT: ScreenerSort = { key: 'company', direction: 'asc' };

function sortValue(row: ScreenerRow, key: SortKey): string | number | null {
  if (key === 'company') {
    return row.ticker;
  }
  const value = columnValue(row, key);
  if (value === null) {
    return null;
  }

  const kind = findColumn(key)?.kind;
  const isText = kind === 'text' || kind === 'date';
  // Order only, unlike display: six decimals fit a double without changing the order.
  return isText ? value : Number(value);
}

function compareValues(first: string | number, second: string | number): number {
  if (typeof first === 'number' && typeof second === 'number') {
    return first - second;
  }
  return String(first).localeCompare(String(second), 'en');
}

/** Rows in the chosen order: empty values always last, ties by ticker in both directions. */
export function sortRows(rows: ScreenerRow[], sort: ScreenerSort): ScreenerRow[] {
  const directionFactor = sort.direction === 'asc' ? 1 : -1;

  return [...rows].sort((first, second) => {
    const firstValue = sortValue(first, sort.key);
    const secondValue = sortValue(second, sort.key);

    if (firstValue === null && secondValue !== null) {
      return 1;
    }
    if (firstValue !== null && secondValue === null) {
      return -1;
    }
    if (firstValue !== null && secondValue !== null) {
      const difference = compareValues(firstValue, secondValue);
      if (difference !== 0) {
        return difference * directionFactor;
      }
    }
    return first.ticker.localeCompare(second.ticker, 'en');
  });
}

export function countMissing(rows: ScreenerRow[], key: SortKey): number {
  return rows.filter((row) => sortValue(row, key) === null).length;
}

export function sortKeyLabel(key: SortKey): string {
  if (key === 'company') {
    return 'Company';
  }
  return findColumn(key)?.label ?? key;
}

/** Names read from A to Z; numbers and dates start from the largest, the usual question. */
export function defaultDirection(key: SortKey): SortDirection {
  return key === 'company' || key === 'sector' ? 'asc' : 'desc';
}

/** The sort after selecting a column: the same column reverses, another starts afresh. */
export function nextSort(current: ScreenerSort, key: SortKey): ScreenerSort {
  if (current.key !== key) {
    return { key, direction: defaultDirection(key) };
  }
  return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' };
}
