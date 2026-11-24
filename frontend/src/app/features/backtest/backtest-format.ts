// How the backtest page reads and writes its numbers. Returns arrive as JSON numbers computed
// from the vendor's floating-point adjusted closes, so plain number formatting is exact enough.
import {
  BacktestPortfolio,
  CataloguePeriod,
  RatioBacktest,
  RatioPeriod,
  ReturnSummary,
} from '../../models/backtest';
import { RatioKey } from '../../models/screener';
import { formatDate } from '../../shared/date-format';
import { formatRatio, RATIO_DEFINITIONS, RatioDefinition } from '../../shared/ratio-definitions';

/** Returns before or after the 0.10% cost of each trade. */
export type Costs = 'gross' | 'net';

/**
 * The ratios the backtest ranks by, in the screener's column order. Operating margin is left
 * out: it is empty for whole sectors, so its ranking would hold a different set of companies.
 */
export const BACKTEST_RATIO_KEYS: RatioKey[] = RATIO_DEFINITIONS.map(
  (definition) => definition.key,
).filter((key) => key !== 'operating_margin');

export const DEFAULT_BACKTEST_RATIO: RatioKey = 'pe';
export const DEFAULT_COSTS: Costs = 'net';
// A year in the address, as "2018": the year of a ranking date.
const YEAR_PATTERN = /^\d{4}$/;

/**
 * What else a quintile holds where a small positive denominator makes a ratio very large
 * (review of 18.11.2026: 39% of OCF / net income's quintile 1 had a net margin below 5%, half of
 * EPS growth's quintile 1 doubled its EPS, a fifth of ROE's had equity below 10% of assets; the
 * same was true of 23% of P/B's quintile 5 and of at most 2% of its other quintiles). P/B ranks
 * the lowest values first, so such companies land in its quintile 5.
 */
export const QUINTILE_NOTES: Partial<Record<RatioKey, string>> = {
  pb: 'Little equity, often after share buybacks, makes P/B large, so quintile 5 also holds such companies.',
  ocf_to_net_income:
    'Thin profits make this ratio large, so quintile 1 also holds companies that barely earned.',
  eps_growth:
    'Growth from a weak year is large, so quintile 1 also holds companies recovering from one.',
  roe: 'Little equity, often after share buybacks, makes ROE large, so quintile 1 also holds such companies.',
};

/** The ratio named in the address, or P/E for a missing or unknown one. */
export function parseBacktestRatio(text: string | null): RatioKey {
  const known = BACKTEST_RATIO_KEYS.find((key) => key === text);
  return known ?? DEFAULT_BACKTEST_RATIO;
}

/** The year named in the address, or null (the first year) for a missing or malformed one. */
export function parseBacktestYear(text: string | null): string | null {
  if (text === null || !YEAR_PATTERN.test(text)) {
    return null;
  }
  return text;
}

/** The costs named in the address; after costs unless the address says before. */
export function parseBacktestCosts(text: string | null): Costs {
  if (text === 'gross') {
    return 'gross';
  }
  return DEFAULT_COSTS;
}

/** The year of a holding's ranking date, which names it in the address: "2018". */
export function rankingYear(period: { ranking_date: string }): string {
  return period.ranking_date.slice(0, 4);
}

/** The API's ratios in the screener's column order. */
export function inScreenerOrder(ratios: RatioBacktest[]): RatioBacktest[] {
  return [...ratios].sort(
    (first, second) =>
      BACKTEST_RATIO_KEYS.indexOf(first.ratio) - BACKTEST_RATIO_KEYS.indexOf(second.ratio),
  );
}

const PERCENT = 100;
const BILLION = 1_000_000_000;
// Below this a p-value is shown as "<0.001": 1000 random portfolios cannot resolve less.
const SMALLEST_SHOWN_P_VALUE = 0.001;
// Joins the words of a date, and the first date to the dash, so that a narrow column breaks a
// range after the dash and never inside a date ("2 Apr" on one line, "2018" on the next).
const NO_BREAK_SPACE = '\u00a0';

/** A fraction as a percent with one decimal: 0.154 → "15.4%"; never "-0.0%". */
export function formatReturn(value: number): string {
  return `${oneDecimal(value * PERCENT)}%`;
}

/** A difference of two returns in percentage points, always signed: "+5.1", "-6.4". */
export function formatPoints(value: number): string {
  const text = oneDecimal(value * PERCENT);
  if (text.startsWith('-') || text === '0.0') {
    return text;
  }
  return `+${text}`;
}

function oneDecimal(value: number): string {
  const text = value.toFixed(1);
  if (text === '-0.0') {
    return '0.0';
  }
  return text;
}

export function formatPValue(value: number | null): string {
  if (value === null) {
    return '—';
  }
  if (value < SMALLEST_SHOWN_P_VALUE) {
    return '<0.001';
  }
  return value.toFixed(3);
}

/** A market value in USD as whole billions: "1,130 bn". */
export function formatBillions(value: string | null): string {
  if (value === null) {
    return '—';
  }
  const billions = Math.round(Number(value) / BILLION);
  return `${billions.toLocaleString('en-US')} bn`;
}

export function ratioDefinition(key: RatioKey): RatioDefinition {
  const found = RATIO_DEFINITIONS.find((definition) => definition.key === key);
  if (!found) {
    throw new Error(`Unknown ratio ${key}`);
  }
  return found;
}

/** A company's ratio value as the screener shows it. */
export function formatRatioValue(key: RatioKey, value: string): string {
  return formatRatio(ratioDefinition(key).kind, value);
}

export function directionText(lowestFirst: boolean): string {
  return lowestFirst ? 'Lowest values' : 'Highest values';
}

/** The year of a holding, by its ranking: "2017", or "Apr–Dec 2025" for the partial one. */
export function holdingYear(period: { ranking_date: string; partial: boolean }): string {
  const year = period.ranking_date.slice(0, 4);
  if (period.partial) {
    return `Apr–Dec ${year}`;
  }
  return year;
}

export function holdingDates(period: { buy_session: string; sell_session: string }): string {
  const buyDate = formatDate(period.buy_session).replaceAll(' ', NO_BREAK_SPACE);
  const sellDate = formatDate(period.sell_session).replaceAll(' ', NO_BREAK_SPACE);
  return `${buyDate}${NO_BREAK_SPACE}– ${sellDate}`;
}

export function portfolioReturn(portfolio: BacktestPortfolio, costs: Costs): number {
  return costs === 'net' ? portfolio.net_return : portfolio.gross_return;
}

export function spread(period: RatioPeriod, costs: Costs): number {
  return costs === 'net' ? period.spread_net : period.spread_gross;
}

export function annualised(summary: ReturnSummary, costs: Costs): number {
  return costs === 'net' ? summary.annualised_net : summary.annualised_gross;
}

/** A difference in points that the API leaves empty without enough months: "—". */
export function formatOptionalPoints(value: number | null): string {
  if (value === null) {
    return '—';
  }
  return formatPoints(value);
}

/** A t statistic with two decimals, signed like the difference it tests: "-0.81". */
export function formatT(value: number | null): string {
  if (value === null) {
    return '—';
  }
  const text = value.toFixed(2);
  if (text === '-0.00') {
    return '0.00';
  }
  return text;
}

/** Smallest and largest number of ranked companies over the full years: "170–190". */
export function coverageRange(ratio: RatioBacktest): string {
  const counts = fullPeriods(ratio.periods).map((period) => period.coverage.ranked);
  const lowest = Math.min(...counts);
  const highest = Math.max(...counts);
  if (lowest === highest) {
    return String(lowest);
  }
  return `${lowest}–${highest}`;
}

export function fullPeriods<T extends { partial: boolean }>(periods: T[]): T[] {
  return periods.filter((period) => !period.partial);
}

export function partialPeriods<T extends { partial: boolean }>(periods: T[]): T[] {
  return periods.filter((period) => period.partial);
}

/** The catalogue's holding with the same ranking date as the ratio's period. */
export function cataloguePeriod(
  catalogue: CataloguePeriod[],
  period: RatioPeriod,
): CataloguePeriod | undefined {
  return catalogue.find((item) => item.ranking_date === period.ranking_date);
}
