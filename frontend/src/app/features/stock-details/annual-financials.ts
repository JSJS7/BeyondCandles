import { NgTemplateOutlet } from '@angular/common';
import {
  afterNextRender,
  Component,
  computed,
  inject,
  Injector,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { AnnualMetric, AnnualPeriod } from '../../models/stock';
import {
  FIRST_PERIOD_END,
  FIRST_PERIOD_YEAR,
  LAST_PERIOD_END,
  LAST_PERIOD_YEAR,
} from '../../shared/data-window';
import { formatDate } from '../../shared/date-format';
import { METHODOLOGY_URL } from '../../shared/links';
import { labelGuide, ValueLabel } from '../../shared/value-labels';
import {
  ANNUAL_COLUMNS,
  fiscalYearLabel,
  isSplitAdjusted,
  MetricColumn,
  tableValue,
} from './annual-columns';
import { ValueSources } from './value-sources';

/** Every label a table cell can carry; the reading guide above the table explains each (K15). */
export const ANNUAL_CELL_LABELS = [
  'Basic',
  'Continuing ops',
  'Split-adjusted',
  'Calculated',
  'Series break',
] as const satisfies readonly ValueLabel[];
type AnnualCellLabel = (typeof ANNUAL_CELL_LABELS)[number];

interface CellKey {
  periodStart: string;
  periodEnd: string;
  metricKey: string;
}

interface SelectedCell {
  period: AnnualPeriod;
  column: MetricColumn;
  cell: AnnualMetric;
}

@Component({
  selector: 'app-annual-financials',
  imports: [NgTemplateOutlet, ValueSources],
  templateUrl: './annual-financials.html',
  styleUrl: './annual-financials.css',
})
export class AnnualFinancials {
  private readonly injector = inject(Injector);
  private readonly sourcesPanel = viewChild(ValueSources);
  // The cell button that opened the sources; focus returns there on close.
  private sourcesTrigger: HTMLElement | null = null;

  readonly periods = input.required<AnnualPeriod[]>();

  readonly columns = ANNUAL_COLUMNS;
  readonly formatDate = formatDate;
  readonly fiscalYearLabel = fiscalYearLabel;
  readonly tableValue = tableValue;
  readonly methodologyUrl = METHODOLOGY_URL;
  readonly periodYearsText = `${FIRST_PERIOD_YEAR}–${LAST_PERIOD_YEAR}`;
  readonly periodRangeText = `${formatDate(FIRST_PERIOD_END)} to ${formatDate(LAST_PERIOD_END)}`;
  readonly cellLabelGuide = labelGuide(ANNUAL_CELL_LABELS, {
    'Series break':
      'The change from the year before in this table is therefore misleading. Select the value ' +
      'to compare both years with the report that states them in one definition.',
  });

  readonly hasUnverifiedFiscalYears = computed(() =>
    this.periods().some((period) => period.fiscal_year == null),
  );

  readonly missingFiscalYears = computed(() => {
    // Without verified labels, a missing year cannot be told from a mislabelled one.
    if (this.hasUnverifiedFiscalYears()) {
      return [];
    }
    const reportedYears = new Set(this.periods().map((period) => period.fiscal_year));
    const missingYears: number[] = [];
    for (let year = FIRST_PERIOD_YEAR; year <= LAST_PERIOD_YEAR; year++) {
      if (!reportedYears.has(year)) {
        missingYears.push(year);
      }
    }
    return missingYears;
  });

  readonly missingFiscalYearsLabel = computed(() => {
    const years = this.missingFiscalYears();
    return `${years.length === 1 ? 'fiscal year' : 'fiscal years'} ${years.join(', ')}`;
  });

  readonly periodsWithNotes = computed(() => this.periods().filter((period) => period.period_note));

  private readonly selectedKey = signal<CellKey | null>(null);

  // Looked up again in the current periods, so new data never shows stale sources.
  readonly selected = computed<SelectedCell | null>(() => {
    const key = this.selectedKey();
    if (key === null) {
      return null;
    }

    const period = this.periods().find(
      (candidate) =>
        candidate.period_start === key.periodStart && candidate.period_end === key.periodEnd,
    );
    const column = this.columns.find((candidate) => candidate.key === key.metricKey);
    const cell = period?.metrics[key.metricKey];
    if (!period || !column || !cell) {
      return null;
    }
    return { period, column, cell };
  });

  isSelected(period: AnnualPeriod, column: MetricColumn): boolean {
    const key = this.selectedKey();
    return (
      key !== null &&
      key.periodStart === period.period_start &&
      key.periodEnd === period.period_end &&
      key.metricKey === column.key
    );
  }

  toggleSources(period: AnnualPeriod, column: MetricColumn, event: Event): void {
    if (this.isSelected(period, column)) {
      this.closeSources();
      return;
    }

    this.sourcesTrigger = event.currentTarget as HTMLElement;
    this.selectedKey.set({
      periodStart: period.period_start,
      periodEnd: period.period_end,
      metricKey: column.key,
    });
    // The panel sits below the table; moving focus there also scrolls it into view.
    afterNextRender(() => this.sourcesPanel()?.focusHeading(), {
      injector: this.injector,
    });
  }

  closeSources(): void {
    this.selectedKey.set(null);
    const trigger = this.sourcesTrigger;
    this.sourcesTrigger = null;
    if (trigger?.isConnected) {
      trigger.focus();
    }
  }

  /** Short labels that change how a value may be compared or used. */
  cellLabels(column: MetricColumn, cell: AnnualMetric): AnnualCellLabel[] {
    if (cell.status !== 'ready') {
      return [];
    }

    const labels: AnnualCellLabel[] = [];
    if (column.key === 'eps_diluted' && cell.eps_basis === 'basic') {
      labels.push('Basic');
    }
    if (column.key === 'operating_cash_flow' && cell.scope === 'continuing') {
      labels.push('Continuing ops');
    }
    if (isSplitAdjusted(cell)) {
      labels.push('Split-adjusted');
    } else if (cell.calculation) {
      // A split adjustment is also a calculation; it already has its own label.
      labels.push('Calculated');
    }
    if (cell.series_break) {
      labels.push('Series break');
    }
    return labels;
  }
}
