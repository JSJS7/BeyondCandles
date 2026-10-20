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

    expect(fixture.nativeElement.querySelector('h1').textContent).toBe('MSFT');
    expect(fixture.nativeElement.textContent).not.toContain('was not found');
  });

  it('cancels the old request when the selected ticker changes', () => {
    const oldRequest = http.expectOne('http://127.0.0.1:8000/stocks/AAPL');

    routeParams.next(convertToParamMap({ ticker: 'MSFT' }));

    expect(oldRequest.cancelled).toBe(true);
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
});
