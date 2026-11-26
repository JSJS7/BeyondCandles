import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ScreenerService } from './screener.service';

const API = '/api/screener?date=';

describe('Screener requests', () => {
  let screener: ScreenerService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    screener = TestBed.inject(ScreenerService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('asks the API once for a date and answers a second visit from memory', () => {
    // Coming back from a company page must not wait for the same answer again.
    const answers: unknown[] = [];
    screener.getScreener('2025-12-31').subscribe((answer) => answers.push(answer));
    http.expectOne(`${API}2025-12-31`).flush({ rows: [] });

    screener.getScreener('2025-12-31').subscribe((answer) => answers.push(answer));

    http.expectNone(`${API}2025-12-31`);
    expect(answers).toEqual([{ rows: [] }, { rows: [] }]);
  });

  it('asks again after a failed request, so Retry can still work', () => {
    screener.getScreener('2025-12-31').subscribe({ error: () => undefined });
    http.expectOne(`${API}2025-12-31`).flush('down', { status: 503, statusText: 'Unavailable' });

    screener.getScreener('2025-12-31').subscribe();

    http.expectOne(`${API}2025-12-31`).flush({ rows: [] });
  });

  it('cancels a request left before its answer and asks again on the next visit', () => {
    // Choosing another date leaves the old request; it must not be kept alive as the remembered answer.
    const subscription = screener.getScreener('2025-12-31').subscribe();
    const abandoned = http.expectOne(`${API}2025-12-31`);
    subscription.unsubscribe();

    screener.getScreener('2025-12-31').subscribe();

    expect(abandoned.cancelled).toBe(true);
    http.expectOne(`${API}2025-12-31`).flush({ rows: [] });
  });

  it('forgets the oldest date after ten, so memory stays small', () => {
    // Eleven different dates: the first one drops out and is asked for again.
    for (let day = 1; day <= 11; day++) {
      const date = `2025-01-${String(day).padStart(2, '0')}`;
      screener.getScreener(date).subscribe();
      http.expectOne(`${API}${date}`).flush({ rows: [] });
    }

    screener.getScreener('2025-01-11').subscribe();
    http.expectNone(`${API}2025-01-11`);
    screener.getScreener('2025-01-01').subscribe();
    http.expectOne(`${API}2025-01-01`).flush({ rows: [] });
  });
});
