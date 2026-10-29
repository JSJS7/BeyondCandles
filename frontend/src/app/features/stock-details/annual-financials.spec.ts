import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AnnualMetric, AnnualPeriod, FilingValue } from '../../models/stock';
import { AnnualFinancials } from './annual-financials';

const METRIC_ORDER = ['revenue', 'net_income', 'eps_diluted', 'equity', 'operating_cash_flow'];

function missing(): AnnualMetric {
  return { status: 'not_available', value: null, variants: [] };
}

function filingValue(changes: Partial<FilingValue> = {}): FilingValue {
  return {
    source_cik: '0000320193',
    taxonomy: 'us-gaap',
    tag: 'Revenues',
    unit: 'USD',
    value: '100000000',
    period_start: '2020-01-01',
    period_end: '2020-12-31',
    filed: '2021-02-01',
    accession: '0000320193-21-000010',
    form: '10-K',
    ...changes,
  };
}

function period(
  metrics: Partial<Record<string, AnnualMetric>>,
  changes: Partial<AnnualPeriod> = {},
): AnnualPeriod {
  const allMetrics: Record<string, AnnualMetric> = {};
  for (const key of METRIC_ORDER) {
    allMetrics[key] = metrics[key] ?? missing();
  }
  return {
    fiscal_year: 2020,
    period_start: '2020-01-01',
    period_end: '2020-12-31',
    metrics: allMetrics,
    ...changes,
  };
}

describe('Annual financials table', () => {
  let fixture: ComponentFixture<AnnualFinancials>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [AnnualFinancials] }).compileComponents();
    fixture = TestBed.createComponent(AnnualFinancials);
  });

  function render(periods: AnnualPeriod[]): HTMLElement {
    fixture.componentRef.setInput('periods', periods);
    fixture.detectChanges();
    return fixture.nativeElement;
  }

  function cell(metricKey: string, row = 0): HTMLElement {
    const rows = fixture.nativeElement.querySelectorAll('tbody tr');
    return rows[row].querySelectorAll('td')[METRIC_ORDER.indexOf(metricKey)];
  }

  function openSources(metricKey: string, row = 0): HTMLElement {
    cell(metricKey, row).querySelector('button')!.click();
    fixture.detectChanges();
    return fixture.nativeElement.querySelector('.sources-panel');
  }

  it('shows amounts in whole USD millions and the exact value in the sources', () => {
    // The table is for reading trends; the exact reported digits stay one click away.
    const source = filingValue({ value: '391035400000' });
    render([
      period({
        revenue: {
          status: 'ready',
          value: '391035400000',
          variants: [source],
          selected_sources: [source],
        },
      }),
    ]);

    expect(cell('revenue').querySelector('.cell-value')?.textContent).toBe('391,035');
    const headers = fixture.nativeElement.querySelector('thead').textContent;
    expect(headers).toContain('USD millions');
    expect(headers).toContain('USD per share');

    const panel = openSources('revenue');
    expect(panel.querySelector('.exact-value')?.textContent).toBe('391,035,400,000 USD');
    expect(panel.textContent).toContain('391,035,400,000 USD');
  });

  it('lists the selected total first while retaining component sources', () => {
    const total = filingValue({ value: '100000000' });
    const component = filingValue({ tag: 'SalesRevenueGoodsNet', value: '30000000' });
    render([
      period({
        revenue: {
          status: 'ready',
          value: '100000000',
          variants: [component, total],
          selected_tag: 'Revenues',
          selected_sources: [total],
          selection_note: 'Reported total: Total revenues. Components are not summed.',
        },
      }),
    ]);

    expect(cell('revenue').querySelector('.cell-value')?.textContent).toBe('100');
    const panel = openSources('revenue');
    const sources = panel.querySelectorAll('.source-list li');
    expect(panel.querySelector('h4')?.textContent).toContain('SEC sources (2)');
    expect(sources[0].textContent).toContain('Selected value');
    expect(sources[0].textContent).toContain('us-gaap:Revenues');
    expect(sources[1].textContent).not.toContain('Selected value');
    expect(sources[1].textContent).toContain('SalesRevenueGoodsNet');
    expect(panel.textContent).toContain('Components are not summed.');
  });

  it('labels calculated bank revenue and shows both same-filing components', () => {
    // A calculated GAAP total must not look like a single reported SEC fact.
    const interest = filingValue({ tag: 'InterestIncomeExpenseNet', value: '16289000000' });
    const noninterest = filingValue({ tag: 'NoninterestIncome', value: '11046000000' });
    render([
      period({
        revenue: {
          status: 'ready',
          value: '27335000000',
          variants: [interest, noninterest],
          selected_sources: [interest, noninterest],
          calculation: 'Net interest income + noninterest income',
          selection_note: 'Calculated GAAP revenue from the same filing.',
        },
      }),
    ]);

    expect(cell('revenue').textContent).toContain('27,335');
    expect(cell('revenue').textContent).toContain('Calculated');
    const panel = openSources('revenue');
    expect(panel.textContent).toContain('Calculation: Net interest income + noninterest income');
    expect(panel.querySelectorAll('.source-list li').length).toBe(2);
    expect(panel.textContent?.match(/Calculation component/g)?.length).toBe(2);
    expect(panel.textContent).not.toContain('Selected value');
  });

  it('uses the verified fiscal year instead of the calendar year of the end date', () => {
    // FY2015 can end in January 2016 and must not be reported as missing FY2015.
    const element = render([
      period({}, { fiscal_year: 2016, period_start: '2016-01-04', period_end: '2017-01-01' }),
      period({}, { fiscal_year: 2015, period_start: '2014-12-29', period_end: '2016-01-03' }),
    ]);

    const firstRowHeader = element.querySelectorAll('tbody th')[1].textContent;
    expect(firstRowHeader).toContain('FY 2015');
    expect(firstRowHeader).toContain('ends 3 Jan 2016');
    const missingYears = fixture.componentInstance.missingFiscalYears();
    expect(missingYears).not.toContain(2015);
    expect(missingYears).not.toContain(2016);
    expect(missingYears).toContain(2017);
    expect(element.textContent).toContain('fiscal years 2017, 2018');
  });

  it('keeps unknown fiscal labels explicit without inventing missing years', () => {
    // An unverified label must not silently fall back to the end date's calendar year.
    const element = render([period({}, { fiscal_year: null })]);

    expect(element.textContent).toContain('Fiscal year needs review');
    expect(element.textContent).toContain('Missing-year coverage cannot be determined.');
    expect(fixture.componentInstance.missingFiscalYears()).toEqual([]);
  });

  it('shows missing and ambiguous values with their original source details', () => {
    const conflicting = filingValue({ tag: 'ProfitLoss', value: '99000000.000' });
    render([
      period({ net_income: { status: 'needs_review', value: null, variants: [conflicting] } }),
    ]);

    // A value without any source cannot be opened; it is plain text.
    expect(cell('revenue').querySelector('button')).toBeNull();
    expect(cell('revenue').textContent).toContain('Not available');
    expect(cell('net_income').textContent).toContain('Needs review');

    const panel = openSources('net_income');
    expect(panel.textContent).toContain('no value is shown');
    expect(panel.querySelector('.exact-value')).toBeNull();
    expect(panel.textContent).toContain('ProfitLoss');
    expect(panel.textContent).toContain('99,000,000 USD');
    expect(panel.textContent).toContain('2021-02-01');
    expect(panel.textContent).toContain('0000320193-21-000010');
    expect(panel.textContent).toContain('0000320193');
    // Nothing is presented as the used value of an unresolved cell.
    expect(panel.textContent).not.toContain('Selected value');
  });

  it.each([false, true])(
    'identifies net-income sources and calculated values (calculated: %s)',
    (calculated) => {
      // A reported parent value and a derived parent value keep distinct source labels.
      const parent = filingValue({ tag: 'NetIncomeLoss', value: '80000000' });
      const group = filingValue({ tag: 'ProfitLoss', value: '100000000' });
      const minority = filingValue({
        tag: 'NetIncomeLossAttributableToNoncontrollingInterest',
        value: '20000000',
      });
      const formula = 'Group net income - allocations to other owners';
      render([
        period({
          net_income: {
            status: 'ready',
            value: '80000000',
            variants: calculated ? [group, minority] : [group, parent],
            selected_sources: calculated ? [group, minority] : [parent],
            selected_tag: calculated ? null : 'NetIncomeLoss',
            calculation: calculated ? formula : null,
            selection_note: 'Net income attributable to the parent.',
          },
        }),
      ]);

      expect(cell('net_income').querySelector('.cell-value')?.textContent).toBe('80');
      const panel = openSources('net_income');
      const sources = panel.querySelectorAll('.source-list li');
      expect(sources.length).toBe(2);
      if (calculated) {
        expect(cell('net_income').textContent).toContain('Calculated');
        expect(panel.textContent).toContain(formula);
        expect(panel.textContent?.match(/Calculation component/g)?.length).toBe(2);
      } else {
        expect(sources[0].textContent).toContain('Selected value');
        expect(sources[0].textContent).toContain('NetIncomeLoss');
        expect(sources[1].textContent).not.toContain('Selected value');
      }
    },
  );

  it('distinguishes equity columns sharing one SEC concept and links the original statement', () => {
    // Companyfacts and the report's parent column can use the same concept and accession.
    const group = filingValue({
      tag: 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
      value: '100000000',
      period_start: '2020-12-31',
    });
    const parent = {
      ...group,
      value: '80000000',
      statement_url: 'https://www.sec.gov/Archives/R7.htm',
      statement_label: 'Balance at Dec. 31, 2020',
      statement_column: 'Parent [Member]',
    };
    render([
      period({
        equity: {
          status: 'ready',
          value: '80000000',
          variants: [group, parent],
          selected_sources: [parent],
        },
      }),
    ]);

    const panel = openSources('equity');
    const sources = panel.querySelectorAll('.source-list li');
    expect(sources.length).toBe(2);
    expect(sources[0].textContent).toContain('Selected value');
    expect(sources[0].textContent).toContain('Parent [Member]');
    expect(sources[1].textContent).not.toContain('Selected value');
    const statementLink = sources[0].querySelector('a');
    expect(statementLink?.textContent).toBe('Open SEC statement');
    expect(statementLink?.getAttribute('href')).toBe(parent.statement_url);
  });

  it('links each source to its filing folder on SEC EDGAR', () => {
    // The folder path uses the CIK without leading zeros and the accession without dashes.
    const source = filingValue({ accession: '0000320193-25-000079' });
    const withoutIssuer = filingValue({ tag: 'Other', source_cik: null });
    render([
      period({
        revenue: {
          status: 'ready',
          value: '100000000',
          variants: [source, withoutIssuer],
          selected_sources: [source],
        },
      }),
    ]);

    const sources = openSources('revenue').querySelectorAll('.source-list li');
    expect(sources[0].querySelector('a')?.getAttribute('href')).toBe(
      'https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/',
    );
    expect(sources[1].querySelector('a')).toBeNull();
  });

  it.each(['continuing', 'total'] as const)(
    'shows operating cash flow with its reported scope (%s)',
    (scope) => {
      // A continuing subtotal must be visible and explicitly distinguished from whole-company OCF.
      const source = filingValue({
        tag: 'NetCashProvidedByUsedInOperatingActivities',
        value: '-120000000',
      });
      render([
        period({
          operating_cash_flow: {
            status: 'ready',
            value: '-120000000',
            scope,
            variants: [source],
            selected_sources: [source],
            selected_tag: source.tag,
          },
        }),
      ]);

      const ocfCell = cell('operating_cash_flow');
      expect(ocfCell.querySelector('.cell-value')?.textContent).toBe('-120');
      expect(ocfCell.querySelector('.metric-label')?.textContent ?? null).toBe(
        scope === 'continuing' ? 'Continuing ops' : null,
      );
      expect(fixture.nativeElement.querySelectorAll('tbody .metric-label').length).toBe(
        scope === 'continuing' ? 1 : 0,
      );
    },
  );

  it.each(['basic', 'diluted'] as const)(
    'shows one EPS column with its reported basis and split normalization (%s)',
    (basis) => {
      // A reviewed basic fallback uses the same column, with a visible Basic label.
      const source = filingValue({
        tag: basis === 'basic' ? 'EarningsPerShareBasic' : 'EarningsPerShareDiluted',
        unit: 'USD/shares',
        value: '41.27',
        statement_url: 'https://www.sec.gov/Archives/verified/R4.htm',
        statement_label: 'Reported EPS',
        statement_column: '12 months ended 2020-12-31',
      });
      render([
        period({
          eps_diluted: {
            status: 'ready',
            value: '10.3175',
            calculation: 'Reported EPS × 1 / 4',
            eps_share_basis: {
              as_of: '2026-10-13',
              reported_value: '41.27',
              new_shares: 4,
              old_shares: 1,
              applied_splits: [
                {
                  date: '2024-01-01',
                  new_shares: 4,
                  old_shares: 1,
                  source_url: 'https://www.sec.gov/Archives/split.htm',
                },
              ],
            },
            eps_basis: basis,
            variants: [source],
            selected_sources: [source],
            selected_tag: source.tag,
          },
        }),
      ]);

      const headers = Array.from(
        fixture.nativeElement.querySelectorAll('thead th') as NodeListOf<HTMLElement>,
      ).map((header) => header.firstChild?.textContent?.trim());
      expect(headers.filter((label) => label === 'EPS')).toHaveLength(1);

      const epsCell = cell('eps_diluted');
      expect(epsCell.querySelector('.cell-value')?.textContent).toBe('10.3175');
      const labels = Array.from(epsCell.querySelectorAll('.metric-label')).map(
        (label) => label.textContent,
      );
      expect(labels).toEqual(basis === 'basic' ? ['Basic', 'Split-adjusted'] : ['Split-adjusted']);
      // A split adjustment is a calculation, but it is labelled only once.
      expect(labels).not.toContain('Calculated');

      const panel = openSources('eps_diluted');
      expect(panel.textContent).toContain('Reported in the filing: 41.27 USD per share.');
      expect(panel.textContent).toContain('1 Jan 2024');
      expect(panel.textContent).toContain('4 new shares for 1 old');
      expect(panel.textContent).toContain('Share count basis: 13 Oct 2026.');
      expect(panel.querySelector('.share-basis a')?.getAttribute('href')).toBe(
        'https://www.sec.gov/Archives/split.htm',
      );
      expect(panel.textContent).toContain('Calculation component');
      expect(panel.querySelector('.source-list a')?.getAttribute('href')).toBe(
        source.statement_url,
      );
    },
  );

  it('rounds repeating split-adjusted EPS for the table and keeps two decimals', () => {
    // A 3-for-1 split can leave a repeating fraction; whole-dollar EPS keeps its cents.
    render([
      period({
        eps_diluted: { status: 'ready', value: '0.3333333333', variants: [] },
        revenue: missing(),
      }),
      period(
        { eps_diluted: { status: 'ready', value: '6', variants: [] } },
        { fiscal_year: 2019, period_start: '2019-01-01', period_end: '2019-12-31' },
      ),
    ]);

    expect(cell('eps_diluted', 0).textContent?.trim()).toBe('0.3333');
    expect(cell('eps_diluted', 1).textContent?.trim()).toBe('6.00');
  });

  it('marks periods with notes and explains them below the table', () => {
    const element = render([
      period({}, { fiscal_year: 2016, period_note: 'Nominal fiscal year end is 31 October.' }),
      period({}, { fiscal_year: 2015, period_start: '2015-01-01', period_end: '2015-12-31' }),
    ]);

    const rowHeaders = element.querySelectorAll('tbody th');
    expect(rowHeaders[0].querySelector('.note-marker')).not.toBeNull();
    expect(rowHeaders[1].querySelector('.note-marker')).toBeNull();
    const notes = element.querySelector('.period-notes')?.textContent ?? '';
    expect(notes).toContain('FY 2016');
    expect(notes).toContain('Nominal fiscal year end is 31 October.');
  });

  it('opens one source panel at a time, closes it and returns focus to the value', async () => {
    // Keyboard and screen-reader users must get back to the table after reading sources.
    const source = filingValue();
    const ready: AnnualMetric = {
      status: 'ready',
      value: '100000000',
      variants: [source],
      selected_sources: [source],
    };
    render([period({ revenue: ready, net_income: { ...ready } })]);

    const revenueButton = cell('revenue').querySelector('button')!;
    revenueButton.click();
    fixture.detectChanges();
    await fixture.whenStable();
    const heading = fixture.nativeElement.querySelector('#sources-heading');
    expect(heading.textContent).toContain('Revenue · FY 2020');
    expect(document.activeElement).toBe(heading);
    expect(revenueButton.getAttribute('aria-expanded')).toBe('true');

    // Selecting another value replaces the panel instead of adding a second one.
    openSources('net_income');
    expect(fixture.nativeElement.querySelectorAll('.sources-panel').length).toBe(1);
    expect(fixture.nativeElement.querySelector('#sources-heading').textContent).toContain(
      'Net income',
    );
    expect(revenueButton.getAttribute('aria-expanded')).toBe('false');

    const netIncomeButton = cell('net_income').querySelector('button')!;
    fixture.nativeElement.querySelector('.close-button').click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.sources-panel')).toBeNull();
    expect(document.activeElement).toBe(netIncomeButton);
  });

  it('closes the panel with Escape and drops it when new data lacks the cell', () => {
    const source = filingValue();
    const ready: AnnualMetric = {
      status: 'ready',
      value: '100000000',
      variants: [source],
      selected_sources: [source],
    };
    render([period({ revenue: ready })]);

    const panel = openSources('revenue');
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.sources-panel')).toBeNull();

    // Sources of a period that is no longer listed must not stay on screen.
    openSources('revenue');
    render([period({ revenue: ready }, { period_start: '2021-01-01', period_end: '2021-12-31' })]);
    expect(fixture.nativeElement.querySelector('.sources-panel')).toBeNull();
  });
});
