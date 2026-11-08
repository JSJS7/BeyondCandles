import { convertToParamMap } from '@angular/router';
import {
  addressKey,
  isValidValuationDate,
  parseScreenerQuery,
  ScreenerQuery,
  screenerQueryParams,
} from './screener-query';

const DEFAULT_QUERY: ScreenerQuery = {
  date: '2025-12-31',
  sectors: [],
  excludeSectors: false,
  search: '',
  sort: { key: 'company', direction: 'asc' },
  criteria: [],
  hiddenColumns: [],
};

describe('Screener address state', () => {
  it('accepts only real dates inside the range the API serves', () => {
    expect(isValidValuationDate('2016-03-31')).toBe(true);
    expect(isValidValuationDate('2025-12-31')).toBe(true);
    expect(isValidValuationDate('2016-03-30')).toBe(false);
    expect(isValidValuationDate('2026-01-01')).toBe(false);
    expect(isValidValuationDate('2021-02-31')).toBe(false);
    expect(isValidValuationDate('')).toBe(false);
    expect(isValidValuationDate('0002-12-31')).toBe(false);
    expect(isValidValuationDate('31.12.2021')).toBe(false);
  });

  it('uses the defaults for an empty or unknown address', () => {
    expect(parseScreenerQuery(convertToParamMap({}))).toEqual(DEFAULT_QUERY);

    const unknown = convertToParamMap({ date: '2099-01-01', sort: 'name', dir: 'up' });
    expect(parseScreenerQuery(unknown)).toEqual(DEFAULT_QUERY);
  });

  it('reads a sort without a direction with the default direction of that column', () => {
    const query = parseScreenerQuery(convertToParamMap({ sort: 'pe' }));
    expect(query.sort).toEqual({ key: 'pe', direction: 'desc' });
  });

  it('keeps the plain address plain and restores every chosen value', () => {
    const chosen: ScreenerQuery = {
      date: '2021-03-31',
      sectors: ['Financial Services', 'Real Estate'],
      excludeSectors: true,
      search: 'bank',
      sort: { key: 'roe', direction: 'asc' },
      criteria: [
        { key: 'pe', min: '', max: '20' },
        { key: 'roe', min: '7.5', max: '' },
      ],
      hiddenColumns: ['price', 'pb'],
    };

    expect(screenerQueryParams(DEFAULT_QUERY)).toEqual({});
    const restored = parseScreenerQuery(convertToParamMap(screenerQueryParams(chosen)));
    expect(restored).toEqual(chosen);
  });

  it('drops unknown, repeated and malformed criteria from the address', () => {
    const query = parseScreenerQuery(
      convertToParamMap({
        // Price is not a ratio, "nope" is no column, the second pe repeats, "abc" is no number.
        filter: ['price:1:2', 'nope:1:2', 'pe:5:10', 'pe:1:2', 'roe:abc:12'],
      }),
    );

    expect(query.criteria).toEqual([
      { key: 'pe', min: '5', max: '10' },
      { key: 'roe', min: '', max: '12' },
    ]);
  });

  it('reads hidden columns once each in table order and ignores unknown names', () => {
    const query = parseScreenerQuery(convertToParamMap({ hide: 'roe,nope,price,roe,,company' }));

    // "company" is no column that can be hidden: the company is always shown.
    expect(query.hiddenColumns).toEqual(['price', 'roe']);
  });

  it('returns a table sorted by a hidden column to the default order', () => {
    // Otherwise the rows would look unsorted, because no header shows the order.
    const hiddenSort = parseScreenerQuery(convertToParamMap({ sort: 'pe', hide: 'pe' }));
    expect(hiddenSort.sort).toEqual({ key: 'company', direction: 'asc' });

    const shownSort = parseScreenerQuery(convertToParamMap({ sort: 'pe', hide: 'roe' }));
    expect(shownSort.sort).toEqual({ key: 'pe', direction: 'desc' });
  });

  it('reads the older single-sector address and ignores an exclusion without sectors', () => {
    // Addresses saved before several sectors were possible keep working.
    const single = parseScreenerQuery(convertToParamMap({ sector: 'Energy' }));
    expect(single.sectors).toEqual(['Energy']);

    const query = parseScreenerQuery(convertToParamMap({ exclude: '1' }));
    expect(query.excludeSectors).toBe(false);
    expect(screenerQueryParams({ ...query, excludeSectors: true })).toEqual({});
  });

  it('writes one comparable text for an address whatever the order of its keys', () => {
    // The page uses it to tell its own address changes from a link that the reader followed.
    const first = convertToParamMap({ q: 'bank', sector: ['Energy', 'Utilities'] });
    const second = convertToParamMap({ sector: ['Energy', 'Utilities'], q: 'bank' });
    const other = convertToParamMap({ sector: ['Energy'], q: 'bank' });

    expect(addressKey(first)).toBe(addressKey(second));
    expect(addressKey(first)).not.toBe(addressKey(other));
  });
});
