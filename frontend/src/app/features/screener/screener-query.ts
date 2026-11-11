// The state of the screener page as kept in its address, so that Back and a shared link
// restore it. Anything unknown in the address falls back to the default.
import { ParamMap, Params } from '@angular/router';
import { EARLIEST_VALUATION_DATE, LATEST_VALUATION_DATE } from '../../shared/data-window';
import { ColumnKey, findColumn, SCREENER_COLUMNS } from './screener-columns';
import { CRITERION_COLUMNS, CriterionInput, isNumberText } from './screener-filters';
import { DEFAULT_SORT, defaultDirection, ScreenerSort, SortKey } from './screener-sort';

export interface ScreenerQuery {
  date: string;
  // No sector means every sector; with `excludeSectors` the listed ones are left out instead.
  sectors: string[];
  excludeSectors: boolean;
  search: string;
  sort: ScreenerSort;
  criteria: CriterionInput[];
  // Columns the user does not want in the table; the company column cannot be hidden.
  hiddenColumns: ColumnKey[];
}

/** A real calendar date inside the range; ISO dates of equal length compare as text. */
export function isValidValuationDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  const isRealDate = !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  if (!isRealDate) {
    return false;
  }
  return value >= EARLIEST_VALUATION_DATE && value <= LATEST_VALUATION_DATE;
}

function parseSort(params: ParamMap): ScreenerSort {
  const key = params.get('sort');
  const isKnownKey = key === 'company' || (key !== null && findColumn(key) !== undefined);
  if (!isKnownKey) {
    return DEFAULT_SORT;
  }

  const sortKey = key as SortKey;
  const direction = params.get('dir');
  if (direction === 'asc' || direction === 'desc') {
    return { key: sortKey, direction };
  }
  return { key: sortKey, direction: defaultDirection(sortKey) };
}

function parseHiddenColumns(params: ParamMap): ColumnKey[] {
  // Only known columns, once each and in table order, whatever the address says.
  const keys = (params.get('hide') ?? '').split(',');
  return SCREENER_COLUMNS.map((column) => column.key).filter((key) => keys.includes(key));
}

function parseCriteria(params: ParamMap): CriterionInput[] {
  const criteria: CriterionInput[] = [];
  for (const entry of params.getAll('filter')) {
    const [key, min = '', max = ''] = entry.split(':');
    const column = CRITERION_COLUMNS.find((candidate) => candidate.key === key);
    const isRepeated = criteria.some((criterion) => criterion.key === key);
    if (!column || isRepeated) {
      continue;
    }
    criteria.push({
      key: column.key,
      min: isNumberText(min) ? min : '',
      max: isNumberText(max) ? max : '',
    });
  }
  return criteria;
}

/** Read the page state from the address; anything unknown falls back to the default. */
export function parseScreenerQuery(params: ParamMap): ScreenerQuery {
  const date = params.get('date');
  const sectors = [...new Set(params.getAll('sector').filter((sector) => sector !== ''))];
  const hiddenColumns = parseHiddenColumns(params);

  let sort = parseSort(params);
  // A table sorted by a column that is not shown would look unsorted.
  if (sort.key !== 'company' && hiddenColumns.includes(sort.key)) {
    sort = DEFAULT_SORT;
  }

  return {
    date: date !== null && isValidValuationDate(date) ? date : LATEST_VALUATION_DATE,
    sectors,
    excludeSectors: sectors.length > 0 && params.get('exclude') === '1',
    search: params.get('q') ?? '',
    sort,
    criteria: parseCriteria(params),
    hiddenColumns,
  };
}

/** The address form of the state; defaults are left out, so the plain address stays plain. */
export function screenerQueryParams(query: ScreenerQuery): Params {
  const params: Params = {};
  if (query.date !== LATEST_VALUATION_DATE) {
    params['date'] = query.date;
  }
  if (query.sectors.length > 0) {
    params['sector'] = query.sectors;
    if (query.excludeSectors) {
      params['exclude'] = '1';
    }
  }
  if (query.search.trim() !== '') {
    params['q'] = query.search;
  }
  if (query.hiddenColumns.length > 0) {
    params['hide'] = query.hiddenColumns.join(',');
  }

  const isDefaultSort =
    query.sort.key === DEFAULT_SORT.key && query.sort.direction === DEFAULT_SORT.direction;
  if (!isDefaultSort) {
    params['sort'] = query.sort.key;
    params['dir'] = query.sort.direction;
  }

  if (query.criteria.length > 0) {
    // One "key:min:max" entry per criterion; the order of adding is kept.
    params['filter'] = query.criteria.map((criterion) => {
      const min = criterion.min.replaceAll(':', '');
      const max = criterion.max.replaceAll(':', '');
      return `${criterion.key}:${min}:${max}`;
    });
  }
  return params;
}

/**
 * The same address, written as one comparable text: every key once, in alphabetical order,
 * with all its values. Two addresses with the same state give the same text.
 */
export function addressKey(params: ParamMap): string {
  const keys = [...params.keys].sort();
  return keys.map((key) => `${key}=${params.getAll(key).join(',')}`).join('&');
}
