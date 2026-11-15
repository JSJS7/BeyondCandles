import {
  afterNextRender,
  Component,
  DestroyRef,
  ElementRef,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { AnnualRatios } from '../../models/screener';
import { FIRST_PERIOD_YEAR, LAST_PERIOD_YEAR } from '../../shared/data-window';
import { formatDate } from '../../shared/date-format';
import { METHODOLOGY_URL } from '../../shared/links';
import {
  AUTOMATIC_MEASURES_NOTE,
  formatRatio,
  NO_VALUE_NOTE,
  RATIO_DEFINITIONS,
  RATIO_GUIDE,
  RatioDefinition,
} from '../../shared/ratio-definitions';
import { RATIO_CELL_LABELS, ratioLabels } from '../../shared/ratio-labels';
import { labelGuide } from '../../shared/value-labels';
import { fiscalYearLabel } from './annual-columns';

/**
 * Every label a cell of this table can carry; the reading guide explains each (K15). A price
 * from the fiscal year's end precedes any later spin-off, so no cell is marked Spin-off.
 */
export const ANNUAL_RATIO_CELL_LABELS = RATIO_CELL_LABELS.filter((label) => label !== 'Spin-off');

/** The text of one ratio cell, or null when it has no value and must show a dash, never zero. */
export function ratioText(period: AnnualRatios, ratio: RatioDefinition): string | null {
  const value = period.ratios[ratio.key];
  if (value === null) {
    return null;
  }
  return formatRatio(ratio.kind, value);
}

/** The company's ratios for every fiscal year, at each year's last close (DEC-41). */
@Component({
  selector: 'app-annual-ratio-table',
  templateUrl: './annual-ratio-table.html',
  styleUrl: './annual-ratio-table.css',
})
export class AnnualRatioTable {
  private readonly scroller = viewChild.required<ElementRef<HTMLElement>>('scroller');

  readonly periods = input.required<AnnualRatios[]>();

  // Whether the table is wider than its area, so that it scrolls sideways. Its width depends
  // on the company: a label such as "Discontinued ops" widens its column, so the table is
  // measured rather than compared with a fixed screen width.
  readonly overflows = signal(false);

  readonly ratios = RATIO_DEFINITIONS;
  readonly formatDate = formatDate;
  readonly fiscalYearLabel = fiscalYearLabel;
  readonly ratioText = ratioText;
  readonly ratioLabels = ratioLabels;
  readonly periodYearsText = `${FIRST_PERIOD_YEAR}–${LAST_PERIOD_YEAR}`;
  readonly methodologyUrl = METHODOLOGY_URL;
  readonly ratioGuide = RATIO_GUIDE;
  readonly noValueNote = NO_VALUE_NOTE;
  readonly automaticMeasuresNote = AUTOMATIC_MEASURES_NOTE;
  readonly cellLabelGuide = labelGuide(ANNUAL_RATIO_CELL_LABELS, {
    Calculated: 'Here it is the share count: (net income − preferred dividends) ÷ EPS.',
    'Series break':
      'Growth still comes from one report that states both years, so it is comparable; the ' +
      'annual financials table above shows both versions.',
  });

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      // Test environments without layout have no ResizeObserver; the hint then stays hidden.
      if (typeof ResizeObserver === 'undefined') {
        return;
      }
      const scroller = this.scroller().nativeElement;
      const measure = () => {
        this.overflows.set(scroller.scrollWidth > scroller.clientWidth);
      };
      const observer = new ResizeObserver(measure);
      observer.observe(scroller);
      // The table changes size with new data or wrapped labels, its area with the window.
      const table = scroller.querySelector('table');
      if (table) {
        observer.observe(table);
      }
      destroyRef.onDestroy(() => observer.disconnect());
    });
  }
}
