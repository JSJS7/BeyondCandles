import { Component, computed, ElementRef, input, output, viewChild } from '@angular/core';
import { AnnualMetric, AnnualPeriod, FilingValue } from '../../models/stock';
import { formatDate } from '../../shared/date-format';
import { formatExact, formatPercent } from '../../shared/decimal-format';
import { fiscalYearLabel, isSplitAdjusted, MetricColumn, tableValue } from './annual-columns';
import { filingIndexUrl } from './sec-filing';

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

/** The SEC sources of one value of the annual table, shown below the table (N100). */
@Component({
  selector: 'app-value-sources',
  templateUrl: './value-sources.html',
  styleUrl: './value-sources.css',
  host: {
    '(keydown.escape)': 'closeWithEscape($event)',
  },
})
export class ValueSources {
  private readonly heading = viewChild.required<ElementRef<HTMLElement>>('sourcesHeading');

  readonly period = input.required<AnnualPeriod>();
  readonly column = input.required<MetricColumn>();
  readonly cell = input.required<AnnualMetric>();
  // The labels of the value as the table shows them.
  readonly labels = input.required<readonly string[]>();
  // Every period of the table, to find the earlier year of a series break.
  readonly periods = input.required<AnnualPeriod[]>();
  readonly closed = output<void>();

  readonly formatDate = formatDate;
  readonly formatExact = formatExact;
  readonly fiscalYearLabel = fiscalYearLabel;

  readonly sources = computed(() => this.sourceEntries(this.cell()));
  readonly growthComparison = computed(() =>
    this.comparisonAtSeriesBreak(this.period(), this.column(), this.cell()),
  );

  /** The panel opens with focus on its heading; moving focus also scrolls it into view. */
  focusHeading(): void {
    this.heading().nativeElement.focus();
  }

  closeWithEscape(event: Event): void {
    // N22: the price chart cancels its range on a document-level Escape; this one ends here.
    event.stopPropagation();
    this.closed.emit();
  }

  /** The exact value with its unit. */
  exactValueWithUnit(column: MetricColumn, value: string): string {
    const unit = column.perShare ? 'USD per share' : 'USD';
    return `${formatExact(value)} ${unit}`;
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

  /**
   * At a series break, the table's two values next to the same years in the report that
   * states both. The reader sees which year was restated, not only two percentages.
   */
  private comparisonAtSeriesBreak(
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
      earlier: tableValue(column, earlierCell.value),
      later: tableValue(column, cell.value),
      change: changeText(earlierCell.value, cell.value),
    };
    const reportRow: GrowthComparisonRow = {
      label: `Annual report filed ${formatDate(report.filed)}`,
      earlier: tableValue(column, report.earlier_value),
      later: tableValue(column, report.later_value),
      change: changeText(report.earlier_value, report.later_value),
    };
    const tableSplitAdjusted = isSplitAdjusted(cell) || isSplitAdjusted(earlierCell);
    return {
      earlierLabel: fiscalYearLabel(earlierPeriod),
      laterLabel: fiscalYearLabel(period),
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
