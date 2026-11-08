import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { StockDetails } from './stock-details';

describe('Stock details', () => {
  let fixture: ComponentFixture<StockDetails>;
  let http: HttpTestingController;
  let routeParams: BehaviorSubject<ReturnType<typeof convertToParamMap>>;

  beforeEach(async () => {
    routeParams = new BehaviorSubject(convertToParamMap({ ticker: 'AAPL' }));

    await TestBed.configureTestingModule({
      imports: [StockDetails],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: ActivatedRoute, useValue: { paramMap: routeParams.asObservable() } },
      ],
    }).compileComponents();

    vi.spyOn(console, 'error').mockImplementation(() => {});
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(StockDetails);
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

  it('shows loaded annual financials in the financial table', () => {
    // Values travel from the HTTP response into the table component unchanged.
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL').flush({
      ticker: 'AAPL',
      name: 'Apple',
      exchange: 'NASDAQ',
      financial_history_note: 'Earlier periods follow a verified accounting predecessor.',
    });
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/prices').flush([]);
    const missing = { status: 'not_available', value: null, variants: [] };
    http.expectOne('http://127.0.0.1:8000/stocks/AAPL/financials/annual').flush([
      {
        fiscal_year: 2024,
        period_start: '2023-10-01',
        period_end: '2024-09-28',
        metrics: {
          revenue: { status: 'ready', value: '391035000000', variants: [] },
          net_income: missing,
          eps_diluted: missing,
          equity: missing,
          operating_cash_flow: missing,
        },
      },
    ]);
    fixture.detectChanges();

    const table: HTMLElement = fixture.nativeElement.querySelector('app-annual-financials table');
    expect(table.textContent).toContain('FY 2024');
    expect(table.textContent).toContain('391,035');
    expect(fixture.nativeElement.textContent).toContain(
      'Earlier periods follow a verified accounting predecessor.',
    );
  });
});
