import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { TimeoutError } from 'rxjs';
import { ApiService } from './api.service';

describe('API request timeout', () => {
  let api: ApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  it('cancels a stalled request after five seconds and reports a timeout to the caller', () => {
    const onError = vi.fn();
    api.get('/stocks').subscribe({ error: onError });
    const request = http.expectOne('http://127.0.0.1:8000/stocks');

    vi.advanceTimersByTime(4999);
    expect(onError).not.toHaveBeenCalled();
    expect(request.cancelled).toBe(false);

    vi.advanceTimersByTime(1);
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.any(TimeoutError));
    expect(request.cancelled).toBe(true);
  });

  it('delivers a timely response without reporting a later timeout', () => {
    const onSuccess = vi.fn();
    const onError = vi.fn();
    api.get('/stocks').subscribe({ next: onSuccess, error: onError });
    http.expectOne('http://127.0.0.1:8000/stocks').flush([]);

    vi.advanceTimersByTime(5000);
    expect(onSuccess).toHaveBeenCalledExactlyOnceWith([]);
    expect(onError).not.toHaveBeenCalled();
  });
});
