import { formatDate } from './date-format';

describe('Date formatting', () => {
  it('writes the day without a leading zero, a three-letter month and the year', () => {
    expect(formatDate('2024-12-20')).toBe('20 Dec 2024');
    expect(formatDate('2016-03-01')).toBe('1 Mar 2016');
  });

  it('uses "Sep" for September, like every other month a three-letter name', () => {
    // The British date format of browsers writes "Sept", which looked out of place.
    expect(formatDate('2025-09-27')).toBe('27 Sep 2025');
  });

  it('keeps the day of the text whatever the time zone of the browser', () => {
    // A Date object would show 31 Dec as 30 Dec in time zones west of UTC.
    expect(formatDate('2025-12-31')).toBe('31 Dec 2025');
  });
});
