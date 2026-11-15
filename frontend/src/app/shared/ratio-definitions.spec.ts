import { formatRatio } from './ratio-definitions';

describe('ratio formats', () => {
  it('rounds a multiple to two decimals, half away from zero, with separators', () => {
    expect(formatRatio('multiple', '36.442357')).toBe('36.44');
    expect(formatRatio('multiple', '9.80192')).toBe('9.80');
    expect(formatRatio('multiple', '1234.565')).toBe('1,234.57');
    expect(formatRatio('multiple', '-0.125')).toBe('-0.13');
  });

  it('shows a percent ratio with one decimal', () => {
    // The API gives 0.269151 for 26.9%; the percent format itself is tested in decimal-format.
    expect(formatRatio('percent', '0.269151')).toBe('26.9%');
  });
});
