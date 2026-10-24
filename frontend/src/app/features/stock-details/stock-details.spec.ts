import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { StockDetailsComponent } from './stock-details';

describe('Stock details', () => {
  let fixture: ComponentFixture<StockDetailsComponent>;
  let http: HttpTestingController;
  let routeParams: BehaviorSubject<ReturnType<typeof convertToParamMap>>;

  beforeEach(async () => {
    routeParams = new BehaviorSubject(convertToParamMap({ ticker: 'AAPL' }));

    await TestBed.configureTestingModule({
      imports: [StockDetailsComponent],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: ActivatedRoute, useValue: { paramMap: routeParams.asObservable() } },
      ],
    }).compileComponents();

    vi.spyOn(console, 'error').mockImplementation(() => {});
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(StockDetailsComponent);
    fixture.detectChanges();
  });

  afterEach(() => {
    // These tests focus on metadata; complete the independent data sections as empty.
    const sectionRequests = http.match(
      (request) => request.url.endsWith('/prices') || request.url.endsWith('/financials/annual'),
    );
    for (const request of sectionRequests) {
      if (!request.cancelled) request.flush([]);
    }
    http.verify();
    vi.restoreAllMocks();
  });

  it('shows a server failure without claiming that the company does not exist', () => {
    const request = http.expectOne('http://127.0.0.1:8000/stocks/AAPL');
    request.flush({}, { status: 500, statusText: 'Internal Server Error' });
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Unable to load stock');
    expect(text).toContain('Failed to load stock details.');
    expect(text).not.toContain('Stock not found');
  });

  it('loads another company after a previous ticker returned 404', () => {
    const missingRequest = http.expectOne('http://127.0.0.1:8000/stocks/AAPL');
    missingRequest.flush({}, { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Stock "AAPL" was not found.');

    routeParams.next(convertToParamMap({ ticker: 'MSFT' }));
    expect(fixture.componentInstance.stock()).toBeNull();
    expect(fixture.componentInstance.stockLoadState().status).toBe('loading');

    http.expectOne('http://127.0.0.1:8000/stocks/MSFT').flush({
      ticker: 'MSFT',
      name: 'Microsoft Corporation',
      exchange: 'NASDAQ',
    });
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('h1').textContent).toBe('Microsoft Corporation');
    expect(fixture.nativeElement.textContent).not.toContain('was not found');
  });

  it('shows the full company name above ticker, readable exchange and sector', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple Inc.',
      exchange: 'NMS',
      sector: 'Technology',
    });
    fixture.detectChanges();

    const header = fixture.nativeElement.querySelector('.company-header');
    // Visual spacing comes from the CSS gap between elements.
    const metadata = header.querySelector('.company-meta').textContent.replace(/\s/g, '');
    expect(header.querySelector('h1').textContent).toBe('Apple Inc.');
    expect(metadata).toBe('AAPL·NASDAQ·Technology');
    expect(header.textContent).not.toContain('USD');
  });

  it('cancels the old request when the selected ticker changes', () => {
    const oldRequest = http.expectOne('http://127.0.0.1:8000/stocks/AAPL');
    const oldPrices = http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices');
    const oldFinancials = http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual');

    routeParams.next(convertToParamMap({ ticker: 'MSFT' }));

    expect(oldRequest.cancelled).toBe(true);
    expect(oldPrices.cancelled).toBe(true);
    expect(oldFinancials.cancelled).toBe(true);
    http.expectOne('http://127.0.0.1:8000/stocks/MSFT').flush({
      ticker: 'MSFT',
      name: 'Microsoft Corporation',
      exchange: 'NASDAQ',
    });
    expect(fixture.componentInstance.stock()?.ticker).toBe('MSFT');
  });

  it('cancels pending requests and stops observing the route after destruction', () => {
    const pendingRequest = http.expectOne('http://127.0.0.1:8000/stocks/AAPL');

    fixture.destroy();
    routeParams.next(convertToParamMap({ ticker: 'MSFT' }));

    expect(pendingRequest.cancelled).toBe(true);
    http.expectNone('http://127.0.0.1:8000/stocks/MSFT');
  });

  it('keeps financials usable when price history fails', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NASDAQ',
    });
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices').flush(
      {},
      {
        status: 503,
        statusText: 'Unavailable',
      },
    );
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([]);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Failed to load price history.');
    expect(fixture.nativeElement.textContent).toContain('No annual financials are available');
    expect(fixture.componentInstance.financialsLoadState().status).toBe('ready');
  });

  it('preserves decimal digits when formatting financial values', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NASDAQ',
    });
    const formatted = fixture.componentInstance.formatValue('1234567890123.1234567890');
    expect(formatted).toBe('1,234,567,890,123.123456789');
    expect(fixture.componentInstance.formatValue('0.000')).toBe('0');
  });

  it('retries the same company after an outage without reloading successful sections', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush(
      {},
      {
        status: 503,
        statusText: 'Service Unavailable',
      },
    );
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices').flush([]);
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([]);
    fixture.detectChanges();

    fixture.nativeElement.querySelector('.retry-button').click();
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple Inc.',
      exchange: 'NMS',
    });
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('h1').textContent).toBe('Apple Inc.');
    expect(fixture.nativeElement.textContent).not.toContain('Unable to load stock');
    http.expectNone('http://127.0.0.1:8000/stocks/AAPL/prices');
    http.expectNone('http://127.0.0.1:8000/stocks/AAPL/financials/annual');
  });

  it('retries only failed price history and ignores repeated clicks while loading', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NMS',
    });
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices').flush(
      {},
      {
        status: 503,
        statusText: 'Service Unavailable',
      },
    );
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([]);
    fixture.detectChanges();

    fixture.nativeElement.querySelector('[aria-labelledby="price-heading"] .retry-button').click();
    fixture.componentInstance.retryPrices();
    const request = http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices');
    expect(fixture.componentInstance.pricesLoadState().status).toBe('loading');
    expect(fixture.componentInstance.financialsLoadState().status).toBe('ready');
    http.expectNone('http://127.0.0.1:8000/stocks/AAPL');
    http.expectNone('http://127.0.0.1:8000/stocks/AAPL/financials/annual');

    request.flush([]);
    fixture.detectChanges();
    expect(fixture.componentInstance.pricesLoadState().status).toBe('ready');
    expect(fixture.nativeElement.querySelector('.retry-button')).toBeNull();
  });

  it('keeps retrying financials after repeated failures without reloading prices', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NMS',
    });
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices').flush([]);
    const financialsUrl = 'http://127.0.0.1:8000/stocks/AAPL/financials/annual';
    http.expectOne(financialsUrl).flush({}, { status: 503, statusText: 'Service Unavailable' });
    fixture.detectChanges();

    const retrySelector = '[aria-labelledby="financial-heading"] .retry-button';
    fixture.nativeElement.querySelector(retrySelector).click();
    http.expectOne(financialsUrl).flush({}, { status: 503, statusText: 'Service Unavailable' });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector(retrySelector)).not.toBeNull();

    fixture.nativeElement.querySelector(retrySelector).click();
    http.expectOne(financialsUrl).flush([]);
    fixture.detectChanges();
    expect(fixture.componentInstance.financialsLoadState().status).toBe('ready');
    http.expectNone('http://127.0.0.1:8000/stocks/AAPL/prices');
  });

  it('cancels a retry when the selected company changes and retries the new ticker', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush(
      {},
      {
        status: 503,
        statusText: 'Service Unavailable',
      },
    );
    fixture.componentInstance.retryStock();
    const oldRetry = http.expectOne('http://127.0.0.1:8000/stocks/AAPL');

    routeParams.next(convertToParamMap({ ticker: 'MSFT' }));
    expect(oldRetry.cancelled).toBe(true);
    http.expectOne('http://127.0.0.1:8000/stocks/MSFT').flush(
      {},
      {
        status: 503,
        statusText: 'Service Unavailable',
      },
    );
    fixture.componentInstance.retryStock();
    http.expectOne('http://127.0.0.1:8000/stocks/MSFT').flush({
      ticker: 'MSFT',
      name: 'Microsoft Corporation',
      exchange: 'NMS',
    });
    http.expectNone('http://127.0.0.1:8000/stocks/AAPL');
    expect(fixture.componentInstance.stock()?.ticker).toBe('MSFT');
  });

  it('keeps price history usable when financials fail', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NASDAQ',
    });
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices').flush([]);
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush(
      {},
      {
        status: 503,
        statusText: 'Unavailable',
      },
    );
    fixture.detectChanges();

    expect(fixture.componentInstance.pricesLoadState().status).toBe('ready');
    expect(fixture.nativeElement.textContent).toContain('Failed to load annual financials.');
  });

  it('shows the selected total revenue while retaining component sources', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NASDAQ',
    });
    const missing = { status: 'not_available', value: null, variants: [] };
    const total = {
      source_cik: '0000320193',
      taxonomy: 'us-gaap',
      tag: 'Revenues',
      unit: 'USD',
      value: '100',
      period_start: '2020-01-01',
      period_end: '2020-12-31',
      filed: '2021-02-01',
      accession: 'original',
      form: '10-K',
    };
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
      {
        period_start: '2020-01-01',
        period_end: '2020-12-31',
        fiscal_year: 2020,
        metrics: {
          revenue: {
            status: 'ready',
            value: '100',
            variants: [total, { ...total, tag: 'SalesRevenueGoodsNet', value: '30' }],
            selected_tag: 'Revenues',
            selected_sources: [total],
            selection_note: 'Reported total: Total revenues. Components are not summed.',
          },
          net_income: missing,
          equity: missing,
          eps_diluted: missing,
          operating_cash_flow: missing,
        },
      },
    ]);
    fixture.detectChanges();

    const revenueCell: HTMLElement = fixture.nativeElement.querySelector('tbody td');
    expect(revenueCell.querySelector('span')?.textContent).toBe('100');
    expect(revenueCell.textContent).toContain('Sources (2)');
    expect(revenueCell.textContent).toContain('Components are not summed.');
    expect(revenueCell.querySelectorAll('dl')[0].textContent).toContain('Selected value');
    expect(revenueCell.querySelectorAll('dl')[1].textContent).not.toContain('Selected value');
    expect(revenueCell.textContent).toContain('SalesRevenueGoodsNet');
  });

  it('labels calculated bank revenue and shows both same-filing components', () => {
    // A calculated GAAP total must not look like a single reported SEC fact.
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Bank fixture',
      exchange: 'NYSE',
    });
    const missing = { status: 'not_available', value: null, variants: [] };
    const interest = {
      taxonomy: 'us-gaap',
      tag: 'InterestIncomeExpenseNet',
      unit: 'USD',
      value: '16289',
      period_start: '2020-01-01',
      period_end: '2020-12-31',
      filed: '2021-02-01',
      accession: 'same-filing',
      form: '10-K',
    };
    const noninterest = { ...interest, tag: 'NoninterestIncome', value: '11046' };
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
      {
        period_start: '2020-01-01',
        period_end: '2020-12-31',
        fiscal_year: 2020,
        metrics: {
          revenue: {
            status: 'ready',
            value: '27335',
            variants: [interest, noninterest],
            selected_sources: [interest, noninterest],
            calculation: 'Net interest income + noninterest income',
            selection_note: 'Calculated GAAP revenue from the same filing.',
          },
          net_income: missing,
          equity: missing,
          eps_diluted: missing,
          operating_cash_flow: missing,
        },
      },
    ]);
    fixture.detectChanges();
    const revenueCell: HTMLElement = fixture.nativeElement.querySelector('tbody td');
    expect(revenueCell.textContent).toContain('Calculated');
    expect(revenueCell.textContent).toContain('Net interest income + noninterest income');
    expect(revenueCell.querySelectorAll('dt').length).toBeGreaterThan(0);
    expect(revenueCell.textContent).toContain('InterestIncomeExpenseNet');
    expect(revenueCell.textContent).toContain('NoninterestIncome');
    expect(revenueCell.querySelectorAll('dl').length).toBe(2);
    expect(revenueCell.textContent?.match(/Calculation component/g)?.length).toBe(2);
    expect(revenueCell.textContent).toContain('same-filing');
    expect(revenueCell.textContent).not.toContain('Selected value');
  });

  it('uses the verified fiscal year instead of the calendar year of the end date', () => {
    // FY2015 can end in January 2016 and must not be reported as missing FY2015.
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Fiscal calendar fixture',
      exchange: 'NYSE',
    });
    const missing = { status: 'not_available', value: null, variants: [] };
    const metrics = {
      revenue: missing,
      net_income: missing,
      equity: missing,
      eps_diluted: missing,
      operating_cash_flow: missing,
    };
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
      { fiscal_year: 2015, period_start: '2014-12-29', period_end: '2016-01-03', metrics },
      { fiscal_year: 2016, period_start: '2016-01-04', period_end: '2017-01-01', metrics },
    ]);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('FY 2015');
    expect(fixture.nativeElement.textContent).toContain('2016-01-03');
    expect(fixture.componentInstance.missingAnnualYears()).not.toContain(2015);
    expect(fixture.componentInstance.missingAnnualYears()).not.toContain(2016);
    expect(fixture.componentInstance.missingAnnualYears()).toContain(2017);
  });

  it('keeps unknown fiscal labels explicit without inventing missing years', () => {
    // An unverified label must not silently fall back to the end date's calendar year.
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Unknown fiscal calendar',
      exchange: 'NYSE',
    });
    const missing = { status: 'not_available', value: null, variants: [] };
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
      {
        fiscal_year: null,
        period_start: '2015-01-01',
        period_end: '2015-12-31',
        metrics: {
          revenue: missing,
          net_income: missing,
          equity: missing,
          eps_diluted: missing,
          operating_cash_flow: missing,
        },
      },
    ]);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Fiscal year needs review');
    expect(fixture.nativeElement.textContent).toContain(
      'Missing-year coverage cannot be determined.',
    );
    expect(fixture.componentInstance.missingAnnualYears()).toEqual([]);
  });

  it('shows missing and ambiguous financials with original source details', () => {
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NASDAQ',
      financial_history_note: 'Earlier periods follow a verified accounting predecessor.',
    });
    const missing = { status: 'not_available', value: null, variants: [] };
    const variant = {
      source_cik: '0000320193',
      taxonomy: 'us-gaap',
      tag: 'ProfitLoss',
      unit: 'USD',
      value: '99.000',
      period_start: '2020-01-01',
      period_end: '2020-12-31',
      filed: '2021-02-01',
      accession: 'original',
      form: '10-K',
    };
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
      {
        period_start: '2020-01-01',
        period_end: '2020-12-31',
        fiscal_year: 2020,
        metrics: {
          revenue: missing,
          equity: missing,
          eps_diluted: missing,
          operating_cash_flow: missing,
          net_income: { status: 'needs_review', value: null, variants: [variant] },
        },
      },
    ]);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Needs review');
    expect(text).toContain('Not available');
    expect(text).toContain('ProfitLoss');
    expect(text).toContain('2021-02-01');
    expect(text).toContain('original');
    expect(text).toContain('0000320193');
    expect(text).toContain('Earlier periods follow a verified accounting predecessor.');
    expect(text).toContain('fiscal years 2015, 2016');
    expect(fixture.componentInstance.missingAnnualYears()).not.toContain(2020);
  });

  it.each([false, true])(
    'identifies net-income sources and calculated values (calculated: %s)',
    (calculated) => {
      // A reported parent value and a derived parent value keep distinct source labels.
      http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
        ticker: 'AAPL',
        name: 'Apple',
        exchange: 'NASDAQ',
      });
      const parent = {
        source_cik: '0000320193',
        taxonomy: 'us-gaap',
        tag: 'NetIncomeLoss',
        unit: 'USD',
        value: '80',
        period_start: '2020-01-01',
        period_end: '2020-12-31',
        filed: '2021-02-01',
        accession: 'original',
        form: '10-K',
      };
      const group = { ...parent, tag: 'ProfitLoss', value: '100' };
      const minority = {
        ...parent,
        tag: 'NetIncomeLossAttributableToNoncontrollingInterest',
        value: '20',
      };
      const missing = { status: 'not_available', value: null, variants: [] };
      const formula = 'Group net income - allocations to other owners';
      http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
        {
          fiscal_year: 2020,
          period_start: '2020-01-01',
          period_end: '2020-12-31',
          metrics: {
            revenue: missing,
            equity: missing,
            eps_diluted: missing,
            operating_cash_flow: missing,
            net_income: {
              status: 'ready',
              value: '80',
              variants: calculated ? [group, minority] : [parent, group],
              selected_sources: calculated ? [group, minority] : [parent],
              selected_tag: calculated ? null : 'NetIncomeLoss',
              calculation: calculated ? formula : null,
              selection_note: 'Net income attributable to the parent.',
            },
          },
        },
      ]);
      fixture.detectChanges();
      const cell: HTMLElement = fixture.nativeElement.querySelectorAll('tbody td')[1];
      expect(cell.querySelector('span')?.textContent).toBe('80');
      expect(cell.querySelectorAll('dl').length).toBe(2);
      expect(cell.textContent).toContain(calculated ? 'Calculation component' : 'Selected value');
      expect(cell.textContent).not.toContain('Used for total revenue');
      expect(fixture.nativeElement.textContent).toContain(
        'Net income is attributable to the parent',
      );
      if (calculated) {
        expect(cell.textContent).toContain('Calculated');
        expect(cell.textContent).toContain(formula);
      } else {
        expect(cell.querySelectorAll('dl')[1].textContent).not.toContain('Selected value');
      }
    },
  );
  it('distinguishes equity columns sharing one SEC concept and links the original statement', () => {
    // Companyfacts and the report's parent column can use the same concept and accession.
    http
      .expectOne('http://127.0.0.1:8000/stocks/AAPL')
      .flush({ ticker: 'AAPL', name: 'Apple', exchange: 'NASDAQ' });
    const group = {
      source_cik: '0000320193',
      taxonomy: 'us-gaap',
      tag: 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
      unit: 'USD',
      value: '100',
      period_start: '2020-12-31',
      period_end: '2020-12-31',
      filed: '2021-02-01',
      accession: 'original',
      form: '10-K',
    };
    const parent = {
      ...group,
      value: '80',
      statement_url: 'https://www.sec.gov/Archives/R7.htm',
      statement_label: 'Balance at Dec. 31, 2020',
      statement_column: 'Parent [Member]',
    };
    const missing = { status: 'not_available', value: null, variants: [] };
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
      {
        fiscal_year: 2020,
        period_start: '2020-01-01',
        period_end: '2020-12-31',
        metrics: {
          revenue: missing,
          net_income: missing,
          eps_diluted: missing,
          operating_cash_flow: missing,
          equity: {
            status: 'ready',
            value: '80',
            variants: [group, parent],
            selected_sources: [parent],
          },
        },
      },
    ]);
    fixture.detectChanges();
    const cell: HTMLElement = fixture.nativeElement.querySelectorAll('tbody td')[3];
    const sources = cell.querySelectorAll('dl');
    expect(sources.length).toBe(2);
    expect(sources[0].textContent).not.toContain('Selected value');
    expect(sources[1].textContent).toContain('Selected value');
    expect(sources[1].textContent).toContain('Parent [Member]');
    expect(sources[1].querySelector('a')?.getAttribute('href')).toBe(parent.statement_url);
  });

  it.each(['continuing', 'total'] as const)(
    'shows operating cash flow with its reported scope (%s)',
    (scope) => {
      // A continuing subtotal must be visible and explicitly distinguished from whole-company OCF.
      http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
        ticker: 'AAPL',
        name: 'Apple',
        exchange: 'NASDAQ',
      });
      const source = {
        source_cik: '0000320193',
        taxonomy: 'us-gaap',
        tag: 'NetCashProvidedByUsedInOperatingActivities',
        unit: 'USD',
        value: '-120',
        period_start: '2020-01-01',
        period_end: '2020-12-31',
        filed: '2021-02-01',
        accession: 'original',
        form: '10-K',
      };
      const missing = { status: 'not_available', value: null, variants: [] };
      const note =
        scope === 'continuing'
          ? 'Operating cash flow of continuing operations, excluding discontinued operations.'
          : 'Operating cash flow of the whole company, including discontinued operations.';
      http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
        {
          fiscal_year: 2020,
          period_start: '2020-01-01',
          period_end: '2020-12-31',
          metrics: {
            revenue: missing,
            net_income: missing,
            equity: missing,
            eps_diluted: missing,
            operating_cash_flow: {
              status: 'ready',
              value: '-120',
              scope,
              variants: [source],
              selected_sources: [source],
              selected_tag: source.tag,
              selection_note: note,
            },
          },
        },
      ]);
      fixture.detectChanges();
      const cell: HTMLElement = fixture.nativeElement.querySelectorAll('tbody td')[4];
      expect(cell.querySelector('span')?.textContent).toBe('-120');
      expect(cell.textContent).not.toContain('Not available');
      expect(cell.textContent).toContain('Selected value');
      expect(cell.textContent).toContain(note);
      expect(cell.textContent).toContain(
        scope === 'continuing' ? 'Scope: Continuing operations' : 'Scope: Whole company',
      );
      expect(cell.querySelector('.metric-scope')?.textContent ?? null).toBe(
        scope === 'continuing' ? 'Continuing operations' : null,
      );
      expect(fixture.nativeElement.querySelectorAll('.metric-scope').length).toBe(
        scope === 'continuing' ? 1 : 0,
      );
    },
  );

  it.each(['basic', 'diluted'] as const)(
    'shows one EPS column with its reported basis and split normalization (%s)',
    (basis) => {
      // A reviewed basic fallback uses the same value field, with a visible Basic label and source details.
      http
        .expectOne('http://127.0.0.1:8000/stocks/AAPL')
        .flush({ ticker: 'AAPL', name: 'Apple', exchange: 'NASDAQ' });
      const source = {
        source_cik: '0000320193',
        taxonomy: 'us-gaap',
        tag: basis === 'basic' ? 'EarningsPerShareBasic' : 'EarningsPerShareDiluted',
        unit: 'USD/shares',
        value: '41.27',
        period_start: '2020-01-01',
        period_end: '2020-12-31',
        filed: '2021-02-01',
        accession: 'original',
        form: '10-K',
        statement_url: 'https://www.sec.gov/Archives/verified/R4.htm',
        statement_label: 'Reported EPS',
        statement_column: '12 months ended 2020-12-31',
      };
      const missing = { status: 'not_available', value: null, variants: [] };
      http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
        {
          fiscal_year: 2020,
          period_start: '2020-01-01',
          period_end: '2020-12-31',
          metrics: {
            revenue: missing,
            net_income: missing,
            equity: missing,
            operating_cash_flow: missing,
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
              selection_note:
                basis === 'basic' ? 'Reviewed basic EPS fallback.' : 'Reported diluted net EPS.',
            },
          },
        },
      ]);
      fixture.detectChanges();
      const headers = Array.from(
        fixture.nativeElement.querySelectorAll('thead th') as NodeListOf<HTMLElement>,
      ).map((header) => header.textContent?.trim());
      expect(headers.filter((label) => label === 'EPS')).toHaveLength(1);
      expect(headers).not.toContain('Diluted EPS');
      const cell: HTMLElement = fixture.nativeElement.querySelectorAll('tbody td')[2];
      expect(cell.querySelector('span')?.textContent).toBe('10.3175');
      expect(cell.textContent).not.toContain('Not available');
      expect(cell.textContent).toContain('Calculation component');
      expect(cell.textContent).toContain('Split-adjusted');
      expect(cell.textContent).toContain('Share-count basis: 2026-10-13');
      expect(cell.textContent).toContain('Original EPS: 41.27 USD/share.');
      expect(cell.textContent).toContain('4 new shares for 1 old shares');
      expect(cell.querySelector('a')?.getAttribute('href')).toBe(
        'https://www.sec.gov/Archives/split.htm',
      );
      expect(cell.textContent).toContain(
        basis === 'basic' ? 'EPS basis: Basic' : 'EPS basis: Diluted',
      );
      expect(cell.querySelector('.metric-scope')?.textContent ?? null).toBe(
        basis === 'basic' ? 'Basic' : 'Split-adjusted',
      );
      expect(cell.querySelectorAll('a')[1]?.getAttribute('href')).toBe(source.statement_url);
    },
  );
});
