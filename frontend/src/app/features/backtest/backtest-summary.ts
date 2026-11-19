import { Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';
import { RatioBacktest } from '../../models/backtest';
import { RatioKey } from '../../models/screener';
import {
  annualised,
  Costs,
  coverageRange,
  directionText,
  formatOptionalPoints,
  formatPValue,
  formatReturn,
  formatT,
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
  // A ratio was chosen here, so the page can bring its tables below into view.
  readonly ratioChosen = output<void>();

  readonly costsText = computed(() => (this.costs() === 'net' ? 'after costs' : 'before costs'));

  readonly ratioDefinition = ratioDefinition;
  readonly directionText = directionText;
  readonly coverageRange = coverageRange;
  readonly formatReturn = formatReturn;
  readonly formatOptionalPoints = formatOptionalPoints;
  readonly formatT = formatT;
  readonly formatPValue = formatPValue;
  readonly annualised = annualised;

  /** A plain click opens the ratio on this page; Ctrl, Shift or the middle button open a tab. */
  onRatioClick(event: MouseEvent): void {
    const opensElsewhere =
      event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey;
    if (opensElsewhere) {
      return;
    }
    this.ratioChosen.emit();
  }

  firstQuintile(ratio: RatioBacktest) {
    return ratio.quintiles[0];
  }

  lastQuintile(ratio: RatioBacktest) {
    return ratio.quintiles[ratio.quintiles.length - 1];
  }
}
