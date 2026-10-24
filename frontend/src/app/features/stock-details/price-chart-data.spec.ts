import { Price } from '../../models/stock';
import {
  formatPeriodChange,
  formatPrice,
  formatPriceDate,
  periodChange,
  pricesForPreset,
  pricesInRange,
  subtractMonths,
} from './price-chart-data';

function price(date: string, adjusted = '100'): Price {
  return {
    date,
    open: '100',
    high: '110',
    low: '90',
    close: '100',
    adjusted_close: adjusted,
    volume: 1000,
  };
}

describe('Price chart ranges and presentation', () => {
  it.each([
    ['2024-03-31', 1, '2024-02-29'],
    ['2023-03-31', 1, '2023-02-28'],
    ['2024-02-29', 12, '2023-02-28'],
    ['2024-01-31', 6, '2023-07-31'],
  ])('clamps %s minus %i months to the valid calendar date %s', (end, months, expected) => {
    expect(subtractMonths(end, months)).toBe(expected);
  });

  it('anchors every preset to the last source date rather than today', () => {
    const dates = [
      '2015-01-02',
      '2019-12-19',
      '2019-12-20',
      '2023-12-20',
      '2024-06-20',
      '2024-11-20',
      '2024-12-20',
    ];
    const prices = dates.map((date) => price(date));
    expect(pricesForPreset(prices, '1M').map((row) => row.date)).toEqual(dates.slice(5));
    expect(pricesForPreset(prices, '6M').map((row) => row.date)).toEqual(dates.slice(4));
    expect(pricesForPreset(prices, '1Y').map((row) => row.date)).toEqual(dates.slice(3));
    expect(pricesForPreset(prices, '5Y').map((row) => row.date)).toEqual(dates.slice(2));
    expect(pricesForPreset(prices, 'All')).toEqual(prices);
    expect(prices.map((row) => row.date)).toEqual(dates);
  });

  it('uses available sessions when a calendar cutoff lands on a weekend', () => {
    const prices = ['2024-06-21', '2024-06-24', '2024-07-22'].map((date) => price(date));
    expect(pricesForPreset(prices, '1M').map((row) => row.date)).toEqual([
      '2024-06-24',
      '2024-07-22',
    ]);
    expect(pricesInRange(prices, { start: '2024-06-24', end: '2024-06-24' })).toEqual([prices[1]]);
    expect(pricesForPreset([], '1M')).toEqual([]);
  });

  it.each([
    ['100', '110', '+10.00%'],
    ['100', '90', '-10.00%'],
    ['100', '100', '0.00%'],
  ])('calculates the adjusted-price change from %s to %s as %s', (first, last, expected) => {
    const prices = [price('2024-01-02', first), price('2024-01-03', last)];
    expect(formatPeriodChange(periodChange(prices))).toBe(expected);
  });

  it('reports unavailable changes for missing data or an invalid starting price', () => {
    expect(periodChange([])).toBeNull();
    expect(periodChange([price('2024-01-02')])).toBeNull();
    expect(periodChange([price('2024-01-02', '0'), price('2024-01-03')])).toBeNull();
    expect(periodChange([price('2024-01-02', 'NaN'), price('2024-01-03')])).toBeNull();
    expect(formatPeriodChange(null)).toBe('Not available');
  });

  it('formats USD and UTC dates consistently and avoids a negative rounded zero', () => {
    expect(formatPrice(1234.5)).toBe('$1,234.50');
    expect(formatPriceDate('2024-12-20')).toBe('20 Dec 2024');
    expect(formatPeriodChange(-0.0001)).toBe('0.00%');
  });
});
