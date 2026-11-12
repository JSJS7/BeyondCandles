import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { StockService } from './stock.service';

describe('Stock requests', () => {
  let stocks: StockService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    stocks = TestBed.inject(StockService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('encodes the ticker from the address, so it cannot reach another endpoint', () => {
    // The ticker comes from the page address and may hold any text a user typed there.
    stocks.getStock('X/prices?a=1').subscribe();

    http.expectOne('http://127.0.0.1:8000/stocks/X%2Fprices%3Fa%3D1').flush({});
  });

  it('keeps a class suffix such as BRK-B readable', () => {
    stocks.getAnnualFinancials('BRK-B').subscribe();

    http.expectOne('http://127.0.0.1:8000/stocks/BRK-B/financials/annual').flush([]);
  });
});
