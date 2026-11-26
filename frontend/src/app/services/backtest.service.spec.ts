import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { BacktestService } from './backtest.service';

const API = '/api/backtest';

describe('Backtest requests', () => {
  let backtest: BacktestService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    backtest = TestBed.inject(BacktestService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('asks for the summary once and answers a second visit from memory', () => {
    const answers: unknown[] = [];
    backtest.getSummary().subscribe((answer) => answers.push(answer));
    http.expectOne(API).flush({ ratios: [] });

    backtest.getSummary().subscribe((answer) => answers.push(answer));

    http.expectNone(API);
    expect(answers).toEqual([{ ratios: [] }, { ratios: [] }]);
  });

  it('remembers each ratio on its own', () => {
    backtest.getRatio('pe').subscribe();
    http.expectOne(`${API}/pe`).flush({ ratios: [] });

    backtest.getRatio('pe').subscribe();
    backtest.getRatio('roa').subscribe();

    http.expectNone(`${API}/pe`);
    http.expectOne(`${API}/roa`).flush({ ratios: [] });
  });

  it('asks again after a failed request, so Try again can still work', () => {
    backtest.getSummary().subscribe({ error: () => undefined });
    http.expectOne(API).flush('down', { status: 503, statusText: 'Unavailable' });

    backtest.getSummary().subscribe();

    http.expectOne(API).flush({ ratios: [] });
  });
});
