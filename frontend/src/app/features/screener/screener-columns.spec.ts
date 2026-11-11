import { ScreenerRow } from '../../models/screener';
import {
  cellLabels,
  cellText,
  columnSummary,
  formatMultiple,
  SCREENER_COLUMNS,
  ScreenerColumn,
  visibleColumns,
} from './screener-columns';
import { period, row } from './screener-test-data';

function column(key: ScreenerColumn['key']): ScreenerColumn {
  const found = SCREENER_COLUMNS.find((candidate) => candidate.key === key);
  if (!found) {
    throw new Error(`Unknown column ${key}`);
  }
  return found;
}

describe('Screener number formats', () => {
  it('rounds a multiple to two decimals, half away from zero, with separators', () => {
    expect(formatMultiple('36.442357')).toBe('36.44');
    expect(formatMultiple('9.80192')).toBe('9.80');
    expect(formatMultiple('1234.565')).toBe('1,234.57');
    expect(formatMultiple('-0.125')).toBe('-0.13');
  });
});

describe('Screener cells', () => {
  it('shows a true zero as zero and an empty value as null, never the other way', () => {
    // The two are different facts: zero growth is a result, an empty cell has no result.
    const company = row('ZERO', { revenue_growth: '0', eps_growth: null });

    expect(cellText(company, column('revenue_growth'))).toBe('0.0%');
    expect(cellText(company, column('eps_growth'))).toBeNull();
  });

  it('leaves every report column empty for a company with no known report', () => {
    const company: ScreenerRow = { ticker: 'NEW', name: 'New', sector: 'Energy', latest: null };

    expect(cellText(company, column('sector'))).toBe('Energy');
    expect(cellText(company, column('price'))).toBeNull();
    expect(cellText(company, column('period_end'))).toBeNull();
    expect(cellText(company, column('pe'))).toBeNull();
    expect(cellLabels(company.latest, 'pe')).toEqual([]);
  });

  it('formats the price in dollars and the period end as a readable date', () => {
    const company = row('AAPL');
    company.latest = period({ price: '271.859985', period_end: '2025-09-27' });

    expect(cellText(company, column('price'))).toBe('$271.86');
    expect(cellText(company, column('period_end'))).toBe('27 Sep 2025');
  });

  it('marks only the ratios that use the marked input', () => {
    const latest = period(
      { eps_basis: 'basic', shares_basis: 'calculated', ocf_scope: 'continuing' },
      { pe: '10', pb: '2', ps: '1', eps_growth: '0.1', fcf_margin: '0.2', roe: '0.1' },
    );

    expect(cellLabels(latest, 'pe')).toEqual(['Basic']);
    expect(cellLabels(latest, 'eps_growth')).toEqual(['Basic']);
    expect(cellLabels(latest, 'pb')).toEqual(['Calculated']);
    expect(cellLabels(latest, 'ps')).toEqual(['Calculated']);
    expect(cellLabels(latest, 'fcf_margin')).toEqual(['Continuing ops']);
    expect(cellLabels(latest, 'roe')).toEqual([]);
    expect(cellLabels(latest, 'sector')).toEqual([]);
  });

  it('marks only the price ratios after a spin-off, next to their other labels', () => {
    const latest = period(
      {
        eps_basis: 'basic',
        spin_offs: [{ event_date: '2020-04-03', separated_fraction: '0.370673' }],
      },
      { pe: '10', pb: '2', ps: '1', roe: '0.1' },
    );

    expect(cellLabels(latest, 'pe')).toEqual(['Basic', 'Spin-off']);
    expect(cellLabels(latest, 'pb')).toEqual(['Spin-off']);
    expect(cellLabels(latest, 'ps')).toEqual(['Spin-off']);
    expect(cellLabels(latest, 'roe')).toEqual([]);
    expect(cellLabels(latest, 'price')).toEqual([]);
  });

  it('marks only P/S of a company with exchangeable units', () => {
    // Net income, EPS and equity belong to the listed shares, so P/E and P/B stay consistent.
    const latest = period({ exchangeable_units: true }, { pe: '10', pb: '2', ps: '1' });

    expect(cellLabels(latest, 'ps')).toEqual(['Exchangeable units']);
    expect(cellLabels(latest, 'pe')).toEqual([]);
    expect(cellLabels(latest, 'pb')).toEqual([]);
  });

  it('puts no input label on an empty ratio, but keeps a series break on empty growth', () => {
    // A break can be the reason why growth is empty, so it stays visible.
    const latest = period({
      shares_basis: 'calculated',
      revenue_basis_break: true,
      eps_basis_break: true,
    });

    expect(cellLabels(latest, 'pb')).toEqual([]);
    expect(cellLabels(latest, 'revenue_growth')).toEqual(['Series break']);
    expect(cellLabels(latest, 'eps_growth')).toEqual(['Series break']);
  });
});

describe('Screener visible columns', () => {
  it('shows every column but the hidden ones, in table order', () => {
    const keys = visibleColumns(['roe', 'price']).map((visible) => visible.key);

    expect(keys).toHaveLength(13);
    expect(keys).not.toContain('price');
    expect(keys.slice(0, 3)).toEqual(['sector', 'period_end', 'pe']);
    expect(visibleColumns([])).toHaveLength(15);
  });

  it('summarises the column choice', () => {
    expect(columnSummary([])).toBe('Columns');
    expect(columnSummary(['pe', 'pb'])).toBe('Columns (13 of 15)');
  });
});
