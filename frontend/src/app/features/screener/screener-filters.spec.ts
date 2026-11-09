import {
  CRITERION_COLUMNS,
  CriterionInput,
  criterionStats,
  criterionStatus,
  criterionUnit,
  exactTickerFirst,
  filterBySearch,
  filterBySector,
  filterRows,
  sectorSummary,
} from './screener-filters';
import { row, tickers } from './screener-test-data';

describe('Screener sector and search filters', () => {
  const rows = [
    row('XOM', {}, 'Energy'),
    row('JPM', {}, 'Financial Services'),
    row('AAPL', {}, 'Technology'),
    row('NONE', {}, null),
  ];

  it('keeps every row when no sector is chosen', () => {
    expect(tickers(filterBySector(rows, [], false))).toEqual(['XOM', 'JPM', 'AAPL', 'NONE']);
    expect(tickers(filterBySector(rows, [], true))).toEqual(['XOM', 'JPM', 'AAPL', 'NONE']);
  });

  it('keeps the chosen sectors, or all others when excluding them', () => {
    const chosen = ['Energy', 'Technology'];
    expect(tickers(filterBySector(rows, chosen, false))).toEqual(['XOM', 'AAPL']);
    // A company without a sector is in no excluded sector, so it stays.
    expect(tickers(filterBySector(rows, chosen, true))).toEqual(['JPM', 'NONE']);
  });

  it('finds a ticker or a part of the name, ignoring case and surrounding spaces', () => {
    expect(tickers(filterBySearch(rows, ' aapl '))).toEqual(['AAPL']);
    // row() names companies "<TICKER> Inc.", so "inc" is in every name.
    expect(tickers(filterBySearch(rows, 'INC'))).toHaveLength(4);
    expect(tickers(filterBySearch(rows, ''))).toHaveLength(4);
    expect(tickers(filterBySearch(rows, 'zzz'))).toEqual([]);
  });

  it('keeps the same companies as the top search, also text inside a word (M35)', () => {
    const companies = [
      { ...row('BA'), name: 'The Boeing Company' },
      { ...row('MSFT'), name: 'Microsoft Corporation' },
      { ...row('WMT'), name: 'Walmart Inc.' },
      { ...row('XOM'), name: 'Exxon Mobil Corporation' },
    ];

    // "soft" and "mart" are inside a word: the top search finds them, so the screener does too.
    expect(tickers(filterBySearch(companies, 'soft'))).toEqual(['MSFT']);
    expect(tickers(filterBySearch(companies, 'mart'))).toEqual(['WMT']);
    expect(tickers(filterBySearch(companies, 'boeing co'))).toEqual(['BA']);
    expect(tickers(filterBySearch(companies, 'apple'))).toEqual([]);
  });

  it('moves the exact ticker to the top and keeps the order of the others', () => {
    const sorted = [row('BA'), row('TGT'), row('T'), row('TMUS')];

    expect(tickers(exactTickerFirst(sorted, ' T '))).toEqual(['T', 'BA', 'TGT', 'TMUS']);
    expect(tickers(exactTickerFirst(sorted, 'tg'))).toEqual(['BA', 'TGT', 'T', 'TMUS']);
    expect(tickers(exactTickerFirst(sorted, ''))).toEqual(['BA', 'TGT', 'T', 'TMUS']);
  });

  it('summarises the sector choice in a few words', () => {
    expect(sectorSummary([], false)).toBe('All sectors');
    expect(sectorSummary(['Energy'], false)).toBe('Energy');
    expect(sectorSummary(['Energy', 'Utilities'], false)).toBe('2 sectors');
    expect(sectorSummary(['Energy'], true)).toBe('All except Energy');
    expect(sectorSummary(['Energy', 'Utilities'], true)).toBe('All except 2 sectors');
  });
});

describe('Screener criterion statistics', () => {
  it('finds the nearest-rank median and middle 80% on a known example', () => {
    // Sorted 1, 2, 3, 4, 100: ranks ceil(0.5) = 1, ceil(2.5) = 3, ceil(4.5) = 5.
    const rows = ['3', '100', '1', '4', '2'].map((pe, index) => row(`T${index}`, { pe }));

    expect(criterionStats(rows, 'pe')).toEqual({ count: 5, p10: '1', median: '3', p90: '100' });
  });

  it('uses only companies with a value, and returns null when none has one', () => {
    const rows = [row('A', { roe: '0.1' }), row('B'), row('C', { roe: '0.3' })];

    expect(criterionStats(rows, 'roe')).toEqual({
      count: 2,
      p10: '0.1',
      median: '0.1',
      p90: '0.3',
    });
    expect(criterionStats([row('B')], 'roe')).toBeNull();
  });

  it('orders by number, not by text', () => {
    // As text "10" < "9"; the median of 9, 10, 11 is 10.
    const rows = ['9', '10', '11'].map((pe, index) => row(`T${index}`, { pe }));
    expect(criterionStats(rows, 'pe')?.median).toBe('10');
  });
});

describe('Screener criteria', () => {
  const rows = [
    row('LOW', { pe: '8', roe: '0.05' }),
    row('MID', { pe: '15', roe: '0.15' }),
    row('HIGH', { pe: '40', roe: '0.3' }),
    row('NONE', { pe: null, roe: '0' }),
  ];

  function matching(criteria: CriterionInput[]): string[] {
    return tickers(filterRows(rows, criteria));
  }

  it('keeps every row when no criterion has a limit', () => {
    expect(matching([])).toEqual(['LOW', 'MID', 'HIGH', 'NONE']);
    expect(matching([{ key: 'pe', min: '', max: '' }])).toEqual(['LOW', 'MID', 'HIGH', 'NONE']);
  });

  it('includes the limits themselves and treats an empty side as no limit', () => {
    expect(matching([{ key: 'pe', min: '8', max: '15' }])).toEqual(['LOW', 'MID']);
    expect(matching([{ key: 'pe', min: '15', max: '' }])).toEqual(['MID', 'HIGH']);
    expect(matching([{ key: 'pe', min: '', max: '15' }])).toEqual(['LOW', 'MID']);
  });

  it('leaves out rows with no value, and does not read an empty value as zero', () => {
    // NONE has no P/E; a maximum of 20 must not let it in as if its P/E were 0.
    expect(matching([{ key: 'pe', min: '', max: '20' }])).not.toContain('NONE');
    // A true zero is a value: NONE has ROE 0, so a maximum of 0 keeps it.
    expect(matching([{ key: 'roe', min: '', max: '0' }])).toEqual(['NONE']);
  });

  it('reads a percent criterion as percent of the ratio, exactly at the boundary', () => {
    // 15 means 15%, the ratio 0.15 of MID. A float product could miss it by 1e-17.
    expect(matching([{ key: 'roe', min: '15', max: '15' }])).toEqual(['MID']);
    expect(matching([{ key: 'roe', min: '15.1', max: '' }])).toEqual(['HIGH']);
    expect(matching([{ key: 'roe', min: '-5', max: '6' }])).toEqual(['LOW', 'NONE']);
  });

  it('requires all criteria at once', () => {
    const criteria: CriterionInput[] = [
      { key: 'pe', min: '', max: '20' },
      { key: 'roe', min: '10', max: '' },
    ];
    expect(matching(criteria)).toEqual(['MID']);
  });

  it('accepts a decimal comma and ignores an invalid side', () => {
    expect(matching([{ key: 'pe', min: '7,5', max: '8,5' }])).toEqual(['LOW']);
    // "abc" is not a limit, so only the valid maximum applies; the field is flagged instead.
    const status = criterionStatus({ key: 'pe', min: 'abc', max: '10' });
    expect(status).toMatchObject({ min: null, max: 10, minInvalid: true, active: true });
    expect(matching([{ key: 'pe', min: 'abc', max: '10' }])).toEqual(['LOW']);
  });

  it('flags a minimum above the maximum, which nothing can satisfy', () => {
    const criterion: CriterionInput = { key: 'pe', min: '20', max: '10' };
    expect(criterionStatus(criterion).reversed).toBe(true);
    expect(matching([criterion])).toEqual([]);
  });

  it('offers only ratios as criteria, with a unit that matches how they are shown', () => {
    const keys = CRITERION_COLUMNS.map((column) => column.key);
    expect(keys).toHaveLength(12);
    expect(keys).not.toContain('price');
    expect(keys).not.toContain('sector');
    expect(keys).not.toContain('period_end');
    expect(criterionUnit('roe')).toBe('%');
    expect(criterionUnit('pe')).toBe('x');
    expect(criterionUnit('ocf_to_net_income')).toBe('x');
  });
});
