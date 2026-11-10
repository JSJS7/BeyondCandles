import { NgTemplateOutlet } from '@angular/common';
import {
  afterNextRender,
  Component,
  computed,
  ElementRef,
  inject,
  Injector,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { AnnualMetric, AnnualPeriod, FilingValue } from '../../models/stock';
import { formatDate } from '../../shared/date-format';
import {
  formatExact,
  formatMillions,
  formatPercent,
  formatPerShare,
} from '../../shared/decimal-format';
import { filingIndexUrl } from './sec-filing';

/** Every label a table cell can carry; the reading guide above the table explains each (K15). */
export const ANNUAL_CELL_LABELS = [
  'Basic',
  'Continuing ops',
  'Split-adjusted',
  'Calculated',
  'Series break',
] as const;
type AnnualCellLabel = (typeof ANNUAL_CELL_LABELS)[number];

interface MetricColumn {
  key: string;
  label: string;
  unit: string;
  perShare: boolean;
}

interface CellKey {
  periodStart: string;
  periodEnd: string;
  metricKey: string;
}

interface SourceEntry {
  variant: FilingValue;
  role: 'Selected value' | 'Calculation component' | null;
  filingUrl: string | null;
}

interface GrowthComparisonRow {
  label: string;
  earlier: string;
  later: string;
  change: string;
}

/** At a series break: the table's two values and the same years in the growth report. */
interface GrowthComparison {
  earlierLabel: string;
  laterLabel: string;
  unit: string;
  rows: GrowthComparisonRow[];
  // Split-adjusted EPS in the table, while the report states EPS as filed.
  reportBeforeSplits: boolean;
}

interface SelectedCell {
  period: AnnualPeriod;
  column: MetricColumn;
  cell: AnnualMetric;
  sources: SourceEntry[];
  growthComparison: GrowthComparison | null;
}

const FIRST_FISCAL_YEAR = 2015;
const LAST_FISCAL_YEAR = 2025;
// Decimal places of a computed change, as the API gives growth (0.028883).
const CHANGE_DECIMALS = 6;

/** A change written with its sign, e.g. +2.9% or -48.4%. */
function signedPercent(ratio: string): string {
  const sign = ratio.startsWith('-') ? '' : '+';
  return `${sign}${formatPercent(ratio)}`;
}

/** The change between two values; none from or into a loss, as for growth. */
function changeText(earlier: string, later: string): string {
  const earlierNumber = Number(earlier);
  const laterNumber = Number(later);
  if (earlierNumber <= 0 || laterNumber <= 0) {
    return 'Not shown';
  }
  const ratio = laterNumber / earlierNumber - 1;
  return signedPercent(ratio.toFixed(CHANGE_DECIMALS));
}

@Component({
  selector: 'app-annual-financials',
  imports: [NgTemplateOutlet],
  templateUrl: './annual-financials.html',
  styleUrl: './annual-financials.css',
})
export class AnnualFinancials {
  private readonly injector = inject(Injector);
  private readonly sourcesHeading = viewChild<ElementRef<HTMLElement>>('sourcesHeading');
  // The cell button that opened the sources; focus returns there on close.
  private sourcesTrigger: HTMLElement | null = null;

  readonly periods = input.required<AnnualPeriod[]>();

  readonly columns: MetricColumn[] = [
    { key: 'revenue', label: 'Revenue', unit: 'USD millions', perShare: false },
    { key: 'net_income', label: 'Net income', unit: 'USD millions', perShare: false },
    { key: 'eps_diluted', label: 'EPS', unit: 'USD per share', perShare: true },
    { key: 'equity', label: 'Parent equity', unit: 'USD millions, year end', perShare: false },
    {
      key: 'operating_cash_flow',
      label: 'Operating cash flow',
      unit: 'USD millions',
      perShare: false,
    },
  ];

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
    for (let year = FIRST_FISCAL_YEAR; year <= LAST_FISCAL_YEAR; year++) {
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

    return {
      period,
      column,
      cell,
      sources: this.sourceEntries(cell),
      growthComparison: this.growthComparison(period, column, cell),
    };
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
    afterNextRender(() => this.sourcesHeading()?.nativeElement.focus(), {
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

  fiscalYearLabel(period: AnnualPeriod): string {
    return period.fiscal_year != null ? `FY ${period.fiscal_year}` : 'Fiscal year needs review';
  }

  formatDate(value: string): string {
    return formatDate(value);
  }

  /** The rounded value shown in the table. */
  tableValue(column: MetricColumn, value: string): string {
    return column.perShare ? formatPerShare(value) : formatMillions(value);
  }

  formatExact(value: string): string {
    return formatExact(value);
  }

  /** The exact value with its unit, as shown in the sources panel. */
  exactValueWithUnit(column: MetricColumn, value: string): string {
    const unit = column.perShare ? 'USD per share' : 'USD';
    return `${formatExact(value)} ${unit}`;
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
    if (this.isSplitAdjusted(cell)) {
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

  /** The change from the year before as one annual report states it, or null without one. */
  growthText(cell: AnnualMetric): string | null {
    const report = cell.growth_report;
    if (!report) {
      return null;
    }
    const source = `both years from annual report ${report.accession}`;
    if (cell.growth == null) {
      return `Change from the previous year: not shown, because a year is zero or negative (${source}).`;
    }
    return `Change from the previous year: ${signedPercent(cell.growth)}, ${source}.`;
  }

  /** N91: the growth report opens on SEC EDGAR like the sources below it. */
  growthReportUrl(cell: AnnualMetric): string | null {
    const report = cell.growth_report;
    if (!report) {
      return null;
    }
    return filingIndexUrl(report.source_cik, report.accession);
  }

  private isSplitAdjusted(cell: AnnualMetric): boolean {
    const appliedSplits = cell.eps_share_basis?.applied_splits.length ?? 0;
    return appliedSplits > 0;
  }

  /**
   * At a series break, the table's two values next to the same years in the report that
   * states both. The reader sees which year was restated, not only two percentages.
   */
  private growthComparison(
    period: AnnualPeriod,
    column: MetricColumn,
    cell: AnnualMetric,
  ): GrowthComparison | null {
    const report = cell.growth_report;
    if (!cell.series_break || !report || cell.value === null) {
      return null;
    }
    const earlierPeriod = this.periods().find(
      (candidate) => candidate.period_end === report.earlier_period_end,
    );
    const earlierCell = earlierPeriod?.metrics[column.key];
    if (!earlierPeriod || !earlierCell || earlierCell.value === null) {
      return null;
    }

    const tableRow: GrowthComparisonRow = {
      label: 'This table',
      earlier: this.tableValue(column, earlierCell.value),
      later: this.tableValue(column, cell.value),
      change: changeText(earlierCell.value, cell.value),
    };
    const reportRow: GrowthComparisonRow = {
      label: `Annual report filed ${formatDate(report.filed)}`,
      earlier: this.tableValue(column, report.earlier_value),
      later: this.tableValue(column, report.later_value),
      change: changeText(report.earlier_value, report.later_value),
    };
    const tableSplitAdjusted = this.isSplitAdjusted(cell) || this.isSplitAdjusted(earlierCell);
    return {
      earlierLabel: this.fiscalYearLabel(earlierPeriod),
      laterLabel: this.fiscalYearLabel(period),
      unit: column.perShare ? 'USD per share' : 'USD millions',
      rows: [tableRow, reportRow],
      reportBeforeSplits: column.perShare && tableSplitAdjusted,
    };
  }

  private sourceEntries(cell: AnnualMetric): SourceEntry[] {
    const entries = cell.variants.map((variant) => ({
      variant,
      role: this.sourceRole(cell, variant),
      filingUrl: variant.source_cik ? filingIndexUrl(variant.source_cik, variant.accession) : null,
    }));
    // Values used by the table come first; alternatives follow in API order.
    const used = entries.filter((entry) => entry.role !== null);
    const alternatives = entries.filter((entry) => entry.role === null);
    return [...used, ...alternatives];
  }

  private sourceRole(cell: AnnualMetric, variant: FilingValue): SourceEntry['role'] {
    if (cell.status !== 'ready') {
      return null;
    }

    const isSelected = (cell.selected_sources ?? []).some(
      (source) =>
        source.source_cik === variant.source_cik &&
        source.taxonomy === variant.taxonomy &&
        source.tag === variant.tag &&
        source.unit === variant.unit &&
        source.accession === variant.accession &&
        source.period_start === variant.period_start &&
        source.period_end === variant.period_end &&
        source.statement_column === variant.statement_column &&
        source.statement_label === variant.statement_label,
    );
    if (!isSelected) {
      return null;
    }
    return cell.calculation ? 'Calculation component' : 'Selected value';
  }
}
