import { Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';
import { QuintileMember, RatioBacktest, RatioPeriod } from '../../models/backtest';
import { formatDate } from '../../shared/date-format';
import { ScrollOverflow } from '../../shared/scroll-overflow';
import {
  Costs,
  formatBillions,
  formatRatioValue,
  formatReturn,
  holdingDates,
  holdingYear,
  portfolioReturn,
  rankingYear,
} from './backtest-format';

/**
 * The companies of every quintile in one year, with a link to the screener on that date: the
 * backtest uses exactly the values the screener shows then.
 */
@Component({
  selector: 'app-backtest-holdings',
  imports: [RouterLink, ScrollOverflow],
  templateUrl: './backtest-holdings.html',
  styleUrl: './backtest-holdings.css',
})
export class BacktestHoldings {
  readonly ratio = input.required<RatioBacktest>();
  readonly costs = input.required<Costs>();

  // The chosen year ("2018"), kept by the page in the address, so that it survives a change of
  // ratio and a return by Back; the first year until the reader picks another.
  readonly chosenYear = input<string | null>(null);
  readonly yearChosen = output<string>();

  readonly period = computed((): RatioPeriod => {
    const periods = this.ratio().periods;
    const chosen = periods.find((period) => rankingYear(period) === this.chosenYear());
    return chosen ?? periods[0];
  });

  /**
   * One row per place in the ranking: the n-th company of every quintile, or null where a
   * quintile is shorter (the first quintiles hold one company more when the count does not
   * divide by five).
   */
  readonly rows = computed((): (QuintileMember | null)[][] => {
    const quintiles = this.period().quintiles;
    let longest = 0;
    for (const quintile of quintiles) {
      longest = Math.max(longest, quintile.members?.length ?? 0);
    }
    const rows: (QuintileMember | null)[][] = [];
    for (let place = 0; place < longest; place++) {
      const row: (QuintileMember | null)[] = [];
      for (const quintile of quintiles) {
        row.push(quintile.members?.[place] ?? null);
      }
      rows.push(row);
    }
    return rows;
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
  readonly rankingYear = rankingYear;

  valueText(value: string): string {
    return formatRatioValue(this.ratio().ratio, value);
  }

  choose(year: string): void {
    this.yearChosen.emit(year);
  }
}
