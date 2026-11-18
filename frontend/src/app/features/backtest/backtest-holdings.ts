import { Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { RatioBacktest, RatioPeriod } from '../../models/backtest';
import { formatDate } from '../../shared/date-format';
import {
  Costs,
  formatBillions,
  formatRatioValue,
  formatReturn,
  holdingDates,
  holdingYear,
  portfolioReturn,
} from './backtest-format';

/**
 * The companies of every quintile in one year, with a link to the same ranking in the screener:
 * the backtest uses exactly the values the screener shows on that date.
 */
@Component({
  selector: 'app-backtest-holdings',
  imports: [RouterLink],
  templateUrl: './backtest-holdings.html',
  styleUrl: './backtest-holdings.css',
})
export class BacktestHoldings {
  readonly ratio = input.required<RatioBacktest>();
  readonly costs = input.required<Costs>();

  // The ranking date of the chosen year; the first year until the reader picks another.
  readonly chosenDate = signal<string | null>(null);

  readonly period = computed((): RatioPeriod => {
    const periods = this.ratio().periods;
    const chosen = periods.find((period) => period.ranking_date === this.chosenDate());
    return chosen ?? periods[0];
  });

  /** Why the other companies are not in the ranking; financial firms only where they count. */
  readonly leftOutText = computed(() => {
    const coverage = this.period().coverage;
    const reasons = [
      `${coverage.empty} without a value`,
      `${coverage.flagged} marked for this ratio`,
    ];
    if (this.ratio().excludes_financial) {
      reasons.push(`${coverage.financial} financial firms`);
    }
    return reasons.join(', ');
  });

  /** The screener on the ranking date, sorted by the ratio in the backtest's order. */
  readonly screenerParams = computed(() => {
    const ratio = this.ratio();
    return {
      date: this.period().ranking_date,
      sort: ratio.ratio,
      dir: ratio.lowest_first ? 'asc' : 'desc',
    };
  });

  readonly formatDate = formatDate;
  readonly formatBillions = formatBillions;
  readonly formatReturn = formatReturn;
  readonly holdingYear = holdingYear;
  readonly holdingDates = holdingDates;
  readonly portfolioReturn = portfolioReturn;

  valueText(value: string): string {
    return formatRatioValue(this.ratio().ratio, value);
  }

  choose(rankingDate: string): void {
    this.chosenDate.set(rankingDate);
  }
}
