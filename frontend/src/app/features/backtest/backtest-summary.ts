import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { RatioBacktest } from '../../models/backtest';
import { RatioKey } from '../../models/screener';
import {
  annualised,
  annualisedSpread,
  Costs,
  coverageRange,
  directionText,
  formatPoints,
  formatPValue,
  formatReturn,
  ratioDefinition,
} from './backtest-format';

/**
 * Every ratio side by side, in the screener's order and without marking a best one: with eleven
 * ratios one of them is likely to look extreme by chance (plan, D7).
 */
@Component({
  selector: 'app-backtest-summary',
  imports: [RouterLink],
  templateUrl: './backtest-summary.html',
  styleUrl: './backtest-summary.css',
})
export class BacktestSummary {
  readonly ratios = input.required<RatioBacktest[]>();
  readonly costs = input.required<Costs>();
  readonly selected = input.required<RatioKey>();

  readonly costsText = computed(() => (this.costs() === 'net' ? 'after costs' : 'before costs'));

  readonly ratioDefinition = ratioDefinition;
  readonly directionText = directionText;
  readonly coverageRange = coverageRange;
  readonly formatReturn = formatReturn;
  readonly formatPoints = formatPoints;
  readonly formatPValue = formatPValue;
  readonly annualised = annualised;
  readonly annualisedSpread = annualisedSpread;

  /** Chance of at least that many positive years from coin tosses, as a percent. */
  coinText(ratio: RatioBacktest): string {
    return formatReturn(ratio.coin_probability);
  }

  firstQuintile(ratio: RatioBacktest) {
    return ratio.quintiles[0];
  }

  lastQuintile(ratio: RatioBacktest) {
    return ratio.quintiles[ratio.quintiles.length - 1];
  }
}
