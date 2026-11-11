// The ratios as every page names and shows them (M38). Definitions and limits are in
// METHODOLOGY (Ratios); the order is the screener's column order.
import { RatioKey } from '../models/screener';

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
