// The fixed window of the dataset (METHODOLOGY, "In short"). Every page builds its dates
// and texts from these values, so a wider window changes this file only (M39).

// Daily prices from the vendor's files.
export const FIRST_PRICE_DATE = '2015-01-02';
export const LAST_PRICE_DATE = '2025-12-31';

// The vendor adjusted the whole price history to the end of its files, not to each day.
export const PRICE_ADJUSTMENT_DATE = '2026-10-13';

// Annual financials: fiscal periods ending in this range.
export const FIRST_PERIOD_END = '2015-01-01';
export const LAST_PERIOD_END = '2025-12-31';
export const FIRST_PERIOD_YEAR = Number(FIRST_PERIOD_END.slice(0, 4));
export const LAST_PERIOD_YEAR = Number(LAST_PERIOD_END.slice(0, 4));

// The range accepted by GET /screener: from the first date when every company has a report
// in the window. The backend validates it as well.
export const EARLIEST_VALUATION_DATE = '2016-03-31';
export const LATEST_VALUATION_DATE = LAST_PRICE_DATE;
