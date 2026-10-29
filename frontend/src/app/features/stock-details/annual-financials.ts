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
import { filingIndexUrl, formatExact, formatMillions, formatPerShare } from './financial-format';
import { formatPriceDate } from './price-chart-data';

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

interface SelectedCell {
  period: AnnualPeriod;
  column: MetricColumn;
  cell: AnnualMetric;
  sources: SourceEntry[];
}

const FIRST_FISCAL_YEAR = 2015;
const LAST_FISCAL_YEAR = 2024;

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
    if (this.hasUnverifiedFiscalYears()) return [];
    const reportedYears = new Set(this.periods().map((period) => period.fiscal_year));
    const missingYears: number[] = [];
    for (let year = FIRST_FISCAL_YEAR; year <= LAST_FISCAL_YEAR; year++) {
      if (!reportedYears.has(year)) missingYears.push(year);
    }
    return missingYears;
  });

  readonly periodsWithNotes = computed(() => this.periods().filter((period) => period.period_note));

  private readonly selectedKey = signal<CellKey | null>(null);

  // Looked up again in the current periods, so new data never shows stale sources.
  readonly selected = computed<SelectedCell | null>(() => {
    const key = this.selectedKey();
    if (key === null) return null;

    const period = this.periods().find(
      (candidate) =>
        candidate.period_start === key.periodStart && candidate.period_end === key.periodEnd,
    );
    const column = this.columns.find((candidate) => candidate.key === key.metricKey);
    const cell = period?.metrics[key.metricKey];
    if (!period || !column || !cell) return null;

    return { period, column, cell, sources: this.sourceEntries(cell) };
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
    return formatPriceDate(value);
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
  cellLabels(column: MetricColumn, cell: AnnualMetric): string[] {
    if (cell.status !== 'ready') return [];

    const labels: string[] = [];
    if (column.key === 'eps_diluted' && cell.eps_basis === 'basic') {
      labels.push('Basic');
    }
    if (column.key === 'operating_cash_flow' && cell.scope === 'continuing') {
      labels.push('Continuing ops');
    }
    const splitAdjusted = (cell.eps_share_basis?.applied_splits.length ?? 0) > 0;
    if (splitAdjusted) {
      labels.push('Split-adjusted');
    } else if (cell.calculation) {
      // A split adjustment is also a calculation; it already has its own label.
      labels.push('Calculated');
    }
    return labels;
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
    if (cell.status !== 'ready') return null;

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
    if (!isSelected) return null;
    return cell.calculation ? 'Calculation component' : 'Selected value';
  }
}
