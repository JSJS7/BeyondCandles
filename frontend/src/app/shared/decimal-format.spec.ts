import {
  formatExact,
  formatFixed,
  formatMillions,
  formatPerShare,
  roundDecimal,
} from './decimal-format';

describe('Financial value formatting', () => {
  it('keeps every reported digit in the exact form', () => {
    // Values beyond floating-point precision must not change.
    expect(formatExact('1234567890123.1234567890')).toBe('1,234,567,890,123.123456789');
    expect(formatExact('0.000')).toBe('0');
    expect(formatExact('-0.0000000000')).toBe('0');
    expect(formatExact('-42012000000')).toBe('-42,012,000,000');
    expect(formatExact('9.77')).toBe('9.77');
  });

  it('rounds half away from zero, including negative values and carries', () => {
    expect(roundDecimal('2.5', 0)).toBe('3');
    expect(roundDecimal('-2.5', 0)).toBe('-3');
    expect(roundDecimal('2.4999', 0)).toBe('2');
    expect(roundDecimal('9.99995', 4)).toBe('10.0000');
    expect(roundDecimal('-0.4', 0)).toBe('0');
    expect(roundDecimal('6', 2)).toBe('6.00');
  });

  it('shows amounts in whole millions', () => {
    // Known examples: Apple FY2024 revenue and a negative JPM operating cash flow.
    expect(formatMillions('391035000000')).toBe('391,035');
    expect(formatMillions('391035000000.0000000000')).toBe('391,035');
    expect(formatMillions('-42012000000')).toBe('-42,012');
    // Amounts reported in thousands round to the nearest million.
    expect(formatMillions('12345678000')).toBe('12,346');
    expect(formatMillions('12345499000')).toBe('12,345');
    expect(formatMillions('882000')).toBe('1');
    expect(formatMillions('0')).toBe('0');
  });

  it('never shows a non-zero amount below half a million as zero', () => {
    // Rounding to 0 would make a small value indistinguishable from an exact zero.
    expect(formatMillions('400000')).toBe('0.4');
    expect(formatMillions('-35000')).toBe('-0.035');
  });

  it('shows per-share amounts with two to four decimals', () => {
    expect(formatPerShare('6')).toBe('6.00');
    expect(formatPerShare('6.0800000000')).toBe('6.08');
    expect(formatPerShare('2.3025')).toBe('2.3025');
    expect(formatPerShare('0.3333333333')).toBe('0.3333');
    expect(formatPerShare('-10.33')).toBe('-10.33');
    expect(formatPerShare('1234.5')).toBe('1,234.50');
  });

  it('rounds to a fixed number of decimals with separators and no negative zero', () => {
    expect(formatFixed('1234.5', 2)).toBe('1,234.50');
    expect(formatFixed('36.442357', 2)).toBe('36.44');
    expect(formatFixed('-0.001', 2)).toBe('0.00');
    expect(formatFixed('1999.5', 0)).toBe('2,000');
  });
});
