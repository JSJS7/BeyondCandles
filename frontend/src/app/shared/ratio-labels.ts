// The labels a ratio cell can carry and when, for every page that shows ratios (M38).
import { PeriodRatios, RatioKey } from '../models/screener';
import { ValueLabel } from './value-labels';

/** Every label a ratio cell can carry; a page's reading guide explains each one it shows (K15). */
export const RATIO_CELL_LABELS = [
  'Basic',
  'Calculated',
  'Series break',
  'Continuing ops',
  'Spin-off',
  'Exchangeable units',
  'Discontinued ops',
] as const satisfies readonly ValueLabel[];
export type RatioCellLabel = (typeof RATIO_CELL_LABELS)[number];

/** Short labels that change how a ratio may be read; the same words as the financials table. */
export function ratioLabels(period: PeriodRatios, key: RatioKey): RatioCellLabel[] {
  // A break can also be the reason why growth is empty, so it does not depend on the value.
  if (key === 'revenue_growth') {
    return period.revenue_basis_break ? ['Series break'] : [];
  }
  if (key === 'eps_growth') {
    const labels: RatioCellLabel[] = period.eps_basis_break ? ['Series break'] : [];
    if (period.ratios.eps_growth !== null && period.eps_basis === 'basic') {
      labels.push('Basic');
    }
    // Growth compares two years, so discontinued operations in either of them distort it.
    const eitherYearDiscontinued =
      period.discontinued_operations || period.previous_discontinued_operations;
    if (period.ratios.eps_growth !== null && eitherYearDiscontinued) {
      labels.push('Discontinued ops');
    }
    return labels;
  }

  if (period.ratios[key] === null) {
    return [];
  }
  const labels: RatioCellLabel[] = [];
  if (key === 'pe' && period.eps_basis === 'basic') {
    labels.push('Basic');
  }
  const usesShareCount = key === 'pb' || key === 'ps';
  if (usesShareCount && period.shares_basis === 'calculated') {
    labels.push('Calculated');
  }
  if (key === 'fcf_margin' && period.ocf_scope === 'continuing') {
    labels.push('Continuing ops');
  }
  const usesPrice = key === 'pe' || key === 'pb' || key === 'ps';
  if (usesPrice && period.spin_offs.length > 0) {
    labels.push('Spin-off');
  }
  if (key === 'ps' && period.exchangeable_units) {
    labels.push('Exchangeable units');
  }
  const usesNetIncome =
    key === 'pe' ||
    key === 'roe' ||
    key === 'roa' ||
    key === 'net_margin' ||
    key === 'ocf_to_net_income';
  if (usesNetIncome && period.discontinued_operations) {
    labels.push('Discontinued ops');
  }
  return labels;
}
