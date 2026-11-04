import { Price } from '../../models/stock';

export type PricePreset = '1M' | '6M' | '1Y' | '5Y' | 'All';
export interface PriceRange {
  start: string;
  end: string;
}

/**
 * One description of the price series wherever it is shown. The vendor adjusted the
 * whole history to its 13 Oct 2026 files, so a level is not the price quoted that day.
 */
export const PRICE_SERIES_DESCRIPTION =
  'USD · Adjusted Close: the vendor adjusted all history for splits, dividends and some ' +
  'spin-offs up to 13 Oct 2026, so levels differ from the prices quoted on each day.';

const priceFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Sessions to label on a multi-year axis: the first of a year, at equal steps of years.
 * The steps are counted back from the last year, so the axis always shows where the
 * series ends. Returns null when the series spans too few years to label by year.
 */
export function yearlyTickIndexes(prices: Price[], maxTicks: number): number[] | null {
  const firstSessions = new Map<string, number>();
  prices.forEach((price, index) => {
    const year = price.date.slice(0, 4);
    if (!firstSessions.has(year)) firstSessions.set(year, index);
  });
  const indexes = [...firstSessions.values()];
  if (indexes.length < 2) return null;
  const step = Math.ceil(indexes.length / maxTicks);
  return indexes.filter((_, position) => (indexes.length - 1 - position) % step === 0);
}

export function formatPrice(value: number): string {
  return Number.isFinite(value) ? priceFormatter.format(value) : 'Not available';
}

export function formatPriceDate(value: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${value}T00:00:00Z`));
}

export function subtractMonths(date: string, months: number): string {
  const source = new Date(`${date}T00:00:00Z`);
  const target = new Date(Date.UTC(source.getUTCFullYear(), source.getUTCMonth() - months, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0));
  target.setUTCDate(Math.min(source.getUTCDate(), lastDay.getUTCDate()));
  return target.toISOString().slice(0, 10);
}

export function pricesInRange(prices: Price[], range: PriceRange | null): Price[] {
  if (!range) return prices;
  return prices.filter((price) => price.date >= range.start && price.date <= range.end);
}

export function pricesForPreset(prices: Price[], preset: PricePreset): Price[] {
  if (preset === 'All' || prices.length === 0) return prices;
  const months = { '1M': 1, '6M': 6, '1Y': 12, '5Y': 60 }[preset];
  const end = prices[prices.length - 1].date;
  return pricesInRange(prices, { start: subtractMonths(end, months), end });
}

export function periodChange(prices: Price[]): number | null {
  if (prices.length < 2) return null;
  const first = Number(prices[0].adjusted_close);
  const last = Number(prices[prices.length - 1].adjusted_close);
  if (!Number.isFinite(first) || !Number.isFinite(last) || first <= 0) return null;
  const change = (last / first - 1) * 100;
  return Number.isFinite(change) ? change : null;
}

export function formatPeriodChange(change: number | null): string {
  if (change === null) return 'Not available';
  // Choose the sign after rounding so a tiny decline does not display as -0.00%.
  const rounded = Math.round(change * 100) / 100;
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(2)}%`;
}
