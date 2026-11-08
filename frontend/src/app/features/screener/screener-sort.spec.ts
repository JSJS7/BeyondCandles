import { countMissing, nextSort, sortRows } from './screener-sort';
import { period, row, tickers } from './screener-test-data';

describe('Screener sorting', () => {
  const rows = [row('A', { pe: '10' }), row('B'), row('C', { pe: '9.5' }), row('D', { pe: '100' })];

  it('compares numbers as numbers, not as text', () => {
    // As text, "100" would come before "9.5".
    const sorted = sortRows(rows, { key: 'pe', direction: 'asc' });
    expect(tickers(sorted)).toEqual(['C', 'A', 'D', 'B']);
  });

  it('keeps empty values last in both directions', () => {
    expect(tickers(sortRows(rows, { key: 'pe', direction: 'asc' })).at(-1)).toBe('B');
    expect(tickers(sortRows(rows, { key: 'pe', direction: 'desc' })).at(-1)).toBe('B');
    expect(tickers(sortRows(rows, { key: 'pe', direction: 'desc' }))).toEqual(['D', 'A', 'C', 'B']);
  });

  it('orders equal and empty values by ticker in both directions, so the order is stable', () => {
    const tied = [row('Z', { pe: '5' }), row('Y'), row('X', { pe: '5' }), row('W')];

    expect(tickers(sortRows(tied, { key: 'pe', direction: 'asc' }))).toEqual(['X', 'Z', 'W', 'Y']);
    expect(tickers(sortRows(tied, { key: 'pe', direction: 'desc' }))).toEqual(['X', 'Z', 'W', 'Y']);
  });

  it('sorts negative values below positive ones', () => {
    const mixed = [row('A', { fcf_margin: '0.1' }), row('B', { fcf_margin: '-0.042' })];
    expect(tickers(sortRows(mixed, { key: 'fcf_margin', direction: 'asc' }))).toEqual(['B', 'A']);
  });

  it('sorts the company column by ticker and the sector column by name', () => {
    const companies = [row('B', {}, 'Utilities'), row('A', {}, 'Energy'), row('C', {}, null)];

    const byCompany = sortRows(companies, { key: 'company', direction: 'desc' });
    expect(tickers(byCompany)).toEqual(['C', 'B', 'A']);

    const bySector = sortRows(companies, { key: 'sector', direction: 'desc' });
    expect(tickers(bySector)).toEqual(['B', 'A', 'C']);
  });

  it('sorts period ends as dates', () => {
    const later = row('A');
    later.latest = period({ period_end: '2025-09-27' });
    const earlier = row('B');
    earlier.latest = period({ period_end: '2024-12-31' });

    const sorted = sortRows([earlier, later], { key: 'period_end', direction: 'desc' });
    expect(tickers(sorted)).toEqual(['A', 'B']);
  });

  it('does not change the input array', () => {
    const input = [row('B'), row('A')];
    sortRows(input, { key: 'company', direction: 'asc' });
    expect(tickers(input)).toEqual(['B', 'A']);
  });

  it('counts the companies without a value in the sorted column', () => {
    expect(countMissing(rows, 'pe')).toBe(1);
    expect(countMissing(rows, 'roe')).toBe(4);
    expect(countMissing(rows, 'company')).toBe(0);
  });

  it('starts numbers from the largest and names from A, and reverses on a second click', () => {
    const start = { key: 'company', direction: 'asc' } as const;

    expect(nextSort(start, 'pe')).toEqual({ key: 'pe', direction: 'desc' });
    expect(nextSort(start, 'sector')).toEqual({ key: 'sector', direction: 'asc' });
    expect(nextSort(start, 'company')).toEqual({ key: 'company', direction: 'desc' });
    expect(nextSort({ key: 'pe', direction: 'desc' }, 'pe')).toEqual({
      key: 'pe',
      direction: 'asc',
    });
  });
});
