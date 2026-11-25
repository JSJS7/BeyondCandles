// The ratios as every page names and shows them (M38). Definitions and limits are in
// METHODOLOGY (Ratios); the order is the screener's column order.
import { RatioKey } from '../models/screener';
import { formatFixed, formatPercent } from './decimal-format';

// A multiple is shown as 12.34, a percent as 12.3%; the API gives both as plain ratios.
export type RatioKind = 'multiple' | 'percent';

export interface RatioDefinition {
  key: RatioKey;
  label: string;
  kind: RatioKind;
  // A shorter table header, so that a table fits a laptop screen; `label` stays the name
  // everywhere else and is what assistive technology reads.
  shortLabel?: string;
}

export const RATIO_DEFINITIONS: RatioDefinition[] = [
  { key: 'pe', label: 'P/E', kind: 'multiple' },
  { key: 'pb', label: 'P/B', kind: 'multiple' },
  { key: 'ps', label: 'P/S', kind: 'multiple' },
  { key: 'roe', label: 'ROE', kind: 'percent' },
  { key: 'roa', label: 'ROA', kind: 'percent' },
  { key: 'net_margin', label: 'Net margin', kind: 'percent' },
  { key: 'operating_margin', label: 'Operating margin', kind: 'percent', shortLabel: 'Op. margin' },
  { key: 'fcf_margin', label: 'FCF margin', kind: 'percent' },
  { key: 'revenue_growth', label: 'Revenue growth', kind: 'percent', shortLabel: 'Rev. growth' },
  { key: 'eps_growth', label: 'EPS growth', kind: 'percent' },
  { key: 'ocf_to_net_income', label: 'OCF / net income', kind: 'multiple' },
  { key: 'equity_to_assets', label: 'Equity / assets', kind: 'percent' },
];

/** A ratio as every page shows it: a multiple as 12.34, a percent as 12.3%. */
export function formatRatio(kind: RatioKind, value: string): string {
  if (kind === 'multiple') {
    return formatFixed(value, 2);
  }
  return formatPercent(value);
}

// The reading guide entries of the ratios, shown by every page with a ratio table (M38).
export const RATIO_GUIDE: { term: string; text: string }[] = [
  {
    term: 'P/E',
    text: 'Price ÷ EPS. Empty when EPS is not positive.',
  },
  {
    term: 'P/B',
    text: 'Price × shares ÷ common equity. Empty when common equity is not positive.',
  },
  {
    term: 'P/S',
    text: 'Price × shares ÷ revenue.',
  },
  {
    term: 'ROE',
    text: '(Net income − preferred dividends) ÷ average common equity.',
  },
  {
    term: 'ROA',
    text: 'Net income ÷ average assets.',
  },
  {
    term: 'Net margin',
    text: 'Net income ÷ revenue.',
  },
  {
    term: 'Operating margin',
    text: 'Operating income ÷ revenue. Many companies, including banks, report no such row.',
  },
  {
    term: 'FCF margin',
    text: '(Operating cash flow − capital expenditure) ÷ revenue.',
  },
  {
    term: 'Revenue, EPS growth',
    text: 'Later ÷ earlier − 1, both years from one report; otherwise empty.',
  },
  {
    term: 'OCF / net income',
    text: 'Operating cash flow ÷ net income. Empty when net income is not positive.',
  },
  {
    term: 'Equity / assets',
    text: 'Reported equity ÷ assets. Not a measure of debt.',
  },
];

// What an empty ratio means, for the dash entry of every reading guide.
export const NO_VALUE_NOTE =
  'No value: an input is missing or the result has no meaning, such as a ratio divided by ' +
  'a value that is not positive or growth from or to one. It never means zero.';

// Which ratios stand on automatic measures (N116). Every page that shows ratios shows this
// sentence in its reading guide, so it is written once here.
export const AUTOMATIC_MEASURES_NOTE =
  'The share count (P/B, P/S), assets (ROA, Equity / assets), operating income (Operating ' +
  'margin), capital expenditure (FCF margin) and preferred stock (P/B, ROE) are automatic ' +
  'measures: selected from SEC data by fixed rules and checked in bulk, not against each ' +
  'filing, so these ratios are less certain.';
