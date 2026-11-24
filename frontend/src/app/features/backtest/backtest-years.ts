import { Component, computed, input } from '@angular/core';
import { CataloguePeriod, RatioBacktest, RatioPeriod, ReturnSummary } from '../../models/backtest';
import {
  annualised,
  cataloguePeriod,
  Costs,
  formatOptionalPoints,
  formatPoints,
  formatPValue,
  formatReturn,
  formatT,
  fullPeriods,
  holdingDates,
  holdingYear,
  partialPeriods,
  portfolioReturn,
  spread,
} from './backtest-format';
import { ScrollOverflow } from '../../shared/scroll-overflow';

/** One ratio's quintiles year by year, with both benchmarks and the random comparison. */
@Component({
  selector: 'app-backtest-years',
  templateUrl: './backtest-years.html',
  styleUrl: './backtest-years.css',
  imports: [ScrollOverflow],
})
export class BacktestYears {
  readonly ratio = input.required<RatioBacktest>();
  readonly catalogue = input.required<CataloguePeriod[]>();
  readonly catalogueSummary = input.required<ReturnSummary>();
  readonly costs = input.required<Costs>();

  readonly fullPeriods = computed(() => fullPeriods(this.ratio().periods));
  readonly partialPeriods = computed(() => partialPeriods(this.ratio().periods));
  readonly costsText = computed(() => (this.costs() === 'net' ? 'after costs' : 'before costs'));
  readonly quintileNumbers = computed(() =>
    this.ratio().quintiles.map((quintile) => quintile.number),
  );

  /** The test of the ranking in one sentence, from the monthly returns before costs. */
  readonly spreadTestText = computed(() => {
    const ratio = this.ratio();
    const mean = formatOptionalPoints(ratio.spread_mean);
    const t = formatT(ratio.spread_t);
    return (
      `Quintile 1 − 5: ${mean} points a year (monthly mean × 12), t ${t}, ` +
      `from ${ratio.spread_months} monthly returns before costs.`
    );
  });

  readonly formatReturn = formatReturn;
  readonly formatPoints = formatPoints;
  readonly formatPValue = formatPValue;
  readonly holdingYear = holdingYear;
  readonly holdingDates = holdingDates;
  readonly portfolioReturn = portfolioReturn;
  readonly spread = spread;
  readonly annualised = annualised;

  /** Return of the whole catalogue in equal parts over the same holding. */
  catalogueText(period: RatioPeriod): string {
    const holding = cataloguePeriod(this.catalogue(), period);
    if (holding === undefined) {
      return '—';
    }
    return formatReturn(portfolioReturn(holding, this.costs()));
  }
}
