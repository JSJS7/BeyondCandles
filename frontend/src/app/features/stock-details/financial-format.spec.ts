import {
  filingIndexUrl,
  formatExact,
  formatMillions,
  formatPerShare,
  roundDecimal,
} from './financial-format';

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

  it('builds the EDGAR filing folder from a CIK and an accession number', () => {
    expect(filingIndexUrl('0000320193', '0000320193-25-000079')).toBe(
      'https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/',
    );
    // Identifiers that are not EDGAR numbers produce no link rather than a broken one.
    expect(filingIndexUrl('0000320193', 'original')).toBeNull();
  });
});
