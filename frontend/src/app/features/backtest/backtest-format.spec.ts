import {
  BACKTEST_RATIO_KEYS,
  coverageRange,
  formatBillions,
  formatOptionalPoints,
  formatT,
  formatPoints,
  formatPValue,
  formatReturn,
  holdingDates,
  holdingYear,
  inScreenerOrder,
  parseBacktestRatio,
} from './backtest-format';
import { ratioBacktest } from './backtest-test-data';

describe('Backtest numbers', () => {
  it('shows returns as percents with one decimal and never a negative zero', () => {
    expect(formatReturn(0.154)).toBe('15.4%');
    expect(formatReturn(-0.0642)).toBe('-6.4%');
    expect(formatReturn(-0.0001)).toBe('0.0%');
  });

  it('signs a difference of returns in points, so its direction is never in doubt', () => {
    expect(formatPoints(0.051)).toBe('+5.1');
    expect(formatPoints(-0.064)).toBe('-6.4');
    expect(formatPoints(0.0004)).toBe('0.0');
  });

  it('shows a p-value below what 1000 portfolios resolve as "<0.001", never 0', () => {
    expect(formatPValue(0.0123)).toBe('0.012');
    expect(formatPValue(0.0004)).toBe('<0.001');
    expect(formatPValue(null)).toBe('—');
  });

  it('shows t with two decimals and an empty test as a dash', () => {
    // Without two months the API has no t; an empty value must not read as zero.
    expect(formatT(-0.8123)).toBe('-0.81');
    expect(formatT(-0.001)).toBe('0.00');
    expect(formatT(null)).toBe('—');
    expect(formatOptionalPoints(-0.045)).toBe('-4.5');
    expect(formatOptionalPoints(null)).toBe('—');
  });

  it('shows a market value in whole billions', () => {
    expect(formatBillions('1129877827017.91879')).toBe('1,130 bn');
    expect(formatBillions(null)).toBe('—');
  });

  it('names the partial last holding by its months', () => {
    expect(holdingYear({ ranking_date: '2017-03-31', partial: false })).toBe('2017');
    expect(holdingYear({ ranking_date: '2025-03-31', partial: true })).toBe('Apr–Dec 2025');
  });

  it('breaks a holding period only after its dash, never inside a date', () => {
    // A narrow year column wrapped "3 Apr 2017 – 2 Apr" and left "2018" alone on a line.
    const dates = holdingDates({ buy_session: '2017-04-03', sell_session: '2018-04-02' });

    const noBreakSpace = '\u00a0';
    expect(dates.replaceAll(noBreakSpace, ' ')).toBe('3 Apr 2017 – 2 Apr 2018');
    // The only ordinary space, where a line may break, follows the dash.
    expect(dates.split(' ')).toEqual(['3\u00a0Apr\u00a02017\u00a0–', '2\u00a0Apr\u00a02018']);
  });
});

describe('Backtest ratios', () => {
  it('ranks eleven ratios: operating margin is left out', () => {
    expect(BACKTEST_RATIO_KEYS.length).toBe(11);
    expect(BACKTEST_RATIO_KEYS).not.toContain('operating_margin');
  });

  it('reads the ratio from the address and falls back to P/E', () => {
    expect(parseBacktestRatio('roa')).toBe('roa');
    expect(parseBacktestRatio('operating_margin')).toBe('pe');
    expect(parseBacktestRatio(null)).toBe('pe');
  });

  it('puts the API ratios in the screener column order', () => {
    const ordered = inScreenerOrder([ratioBacktest('fcf_margin'), ratioBacktest('pe')]);

    expect(ordered.map((ratio) => ratio.ratio)).toEqual(['pe', 'fcf_margin']);
  });

  it('gives the coverage of the full years only', () => {
    const ratio = ratioBacktest('pe');
    ratio.periods[0].coverage.ranked = 170;
    ratio.periods[1].coverage.ranked = 190;
    // The partial year is outside the summary, so its count does not widen the range.
    ratio.periods[2].coverage.ranked = 5;

    expect(coverageRange(ratio)).toBe('170–190');
  });
});
