import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AnnualRatios, Ratios } from '../../models/screener';
import { RATIO_DEFINITIONS, RatioDefinition } from '../../shared/ratio-definitions';
import { period } from '../screener/screener-test-data';
import { ANNUAL_CELL_LABELS } from './annual-financials';
import {
  ANNUAL_RATIO_CELL_LABELS,
  AnnualRatioTable,
  RATIO_ONLY_LABELS,
  ratioText,
} from './annual-ratio-table';

function annualPeriod(
  fiscalYear: number | null,
  periodEnd: string,
  ratios: Partial<Ratios> = {},
): AnnualRatios {
  const start = `${Number(periodEnd.slice(0, 4)) - 1}${periodEnd.slice(4)}`;
  return {
    ...period({ period_start: start, period_end: periodEnd }, ratios),
    fiscal_year: fiscalYear,
  };
}

function definition(key: RatioDefinition['key']): RatioDefinition {
  const found = RATIO_DEFINITIONS.find((candidate) => candidate.key === key);
  if (!found) {
    throw new Error(`Unknown ratio ${key}`);
  }
  return found;
}

describe('Annual ratio cells', () => {
  it('formats a multiple and a percent, and keeps a true zero apart from an empty value', () => {
    // Zero growth is a result; an empty cell has no result and must never read as zero.
    const year = annualPeriod(2024, '2024-12-31', { pe: '20', revenue_growth: '0' });

    expect(ratioText(year, definition('pe'))).toBe('20.00');
    expect(ratioText(year, definition('revenue_growth'))).toBe('0.0%');
    expect(ratioText(year, definition('roe'))).toBeNull();
  });
});

describe('Annual ratio table', () => {
  let fixture: ComponentFixture<AnnualRatioTable>;

  function render(periods: AnnualRatios[]): HTMLElement {
    fixture = TestBed.createComponent(AnnualRatioTable);
    fixture.componentRef.setInput('periods', periods);
    fixture.detectChanges();
    return fixture.nativeElement;
  }

  it('shows the years in the order of the API, newest first, with their labels', () => {
    const element = render([
      annualPeriod(2024, '2024-12-31', { pe: '20' }),
      annualPeriod(null, '2023-12-31', { pe: '15' }),
    ]);

    const rowHeaders = [...element.querySelectorAll('tbody th')].map((cell) => cell.textContent);
    expect(rowHeaders[0]).toContain('FY 2024');
    expect(rowHeaders[0]).toContain('ends 31 Dec 2024');
    // An unverified label is shown as such, never guessed from the dates.
    expect(rowHeaders[1]).toContain('Fiscal year needs review');
  });

  it('has one column per ratio, with the full name for assistive technology', () => {
    const element = render([annualPeriod(2024, '2024-12-31')]);

    const headers = element.querySelectorAll('thead .ratio-column');
    expect(headers.length).toBe(RATIO_DEFINITIONS.length);
    const operatingMargin = headers[RATIO_DEFINITIONS.indexOf(definition('operating_margin'))];
    expect(operatingMargin.querySelector('[aria-hidden="true"]')?.textContent).toBe('Op. margin');
    expect(operatingMargin.querySelector('.visually-hidden')?.textContent).toBe('Operating margin');
  });

  it('shows a dash with a spoken "No value" for an empty ratio', () => {
    const element = render([annualPeriod(2024, '2024-12-31', { pe: '20' })]);

    const cells = element.querySelectorAll('tbody .ratio-cell');
    expect(cells[0].textContent?.trim()).toBe('20.00');
    expect(cells[1].querySelector('.no-value')?.textContent).toBe('—');
    expect(cells[1].querySelector('.visually-hidden')?.textContent).toBe('No value');
  });

  it('marks the cells whose reading a label changes', () => {
    // FY2022 of a company with basic EPS only, units exchangeable into its shares and a large
    // result of discontinued operations: P/E is marked twice, P/S once, P/B not at all.
    const year = annualPeriod(2022, '2022-12-31', { pe: '12', pb: '2', ps: '3' });
    year.eps_basis = 'basic';
    year.exchangeable_units = true;
    year.discontinued_operations = true;
    const element = render([year]);

    const cells = element.querySelectorAll('tbody .ratio-cell');
    const labelsOf = (index: number) =>
      [...cells[index].querySelectorAll('.metric-label')].map((label) => label.textContent);
    expect(labelsOf(0)).toEqual(['Basic', 'Discontinued ops']);
    expect(labelsOf(1)).toEqual([]);
    expect(labelsOf(2)).toEqual(['Exchangeable units']);
  });

  it('explains only the labels the financials never show; their guide explains the rest (K15)', () => {
    // The page explains each label once: shared labels in the financials guide above.
    const element = render([annualPeriod(2024, '2024-12-31')]);

    const guideLabels = [...element.querySelectorAll('.reading-guide dt .metric-label')].map(
      (label) => label.textContent,
    );
    expect(guideLabels).toEqual(['Exchangeable units', 'Share issue', 'Discontinued ops']);
    expect(guideLabels).toEqual([...RATIO_ONLY_LABELS]);
    const explainedOnPage = new Set<string>([...RATIO_ONLY_LABELS, ...ANNUAL_CELL_LABELS]);
    for (const label of ANNUAL_RATIO_CELL_LABELS) {
      expect(explainedOnPage.has(label)).toBe(true);
    }
    // The year-end price precedes any later spin-off, so the history never carries that label.
    expect(ANNUAL_RATIO_CELL_LABELS).not.toContain('Spin-off');
    expect(element.querySelector('.reading-guide')?.textContent).toContain('automatic measures');
  });
});
