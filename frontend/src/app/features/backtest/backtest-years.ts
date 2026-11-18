import { Component, computed, input } from '@angular/core';
import { CataloguePeriod, RatioBacktest, RatioPeriod, ReturnSummary } from '../../models/backtest';
import {
  annualised,
  annualisedSpread,
  cataloguePeriod,
  Costs,
  formatPoints,
  formatPValue,
  formatReturn,
  fullPeriods,
  holdingDates,
  holdingYear,
  partialPeriods,
  portfolioReturn,
  spread,
} from './backtest-format';

/** One ratio's quintiles year by year, with both benchmarks and the random comparison. */
@Component({
  selector: 'app-backtest-years',
  templateUrl: './backtest-years.html',
  styleUrl: './backtest-years.css',
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

  readonly formatReturn = formatReturn;
  readonly formatPoints = formatPoints;
  readonly formatPValue = formatPValue;
  readonly holdingYear = holdingYear;
  readonly holdingDates = holdingDates;
  readonly portfolioReturn = portfolioReturn;
  readonly spread = spread;
  readonly annualised = annualised;
  readonly annualisedSpread = annualisedSpread;

  /** Return of the whole catalogue in equal parts over the same holding. */
  catalogueText(period: RatioPeriod): string {
    const holding = cataloguePeriod(this.catalogue(), period);
    if (holding === undefined) {
      return '—';
    }
    return formatReturn(portfolioReturn(holding, this.costs()));
  }
}
