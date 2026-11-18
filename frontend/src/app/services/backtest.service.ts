import { Injectable, inject } from '@angular/core';
import { Observable, shareReplay } from 'rxjs';
import { BacktestResponse } from '../models/backtest';
import { RatioKey } from '../models/screener';
import { ApiService } from './api.service';

// The API computes the backtest on its first request after a start, which took about 5 s
// (18.11.2026); later requests answer from its memory in milliseconds.
const BACKTEST_TIMEOUT_MS = 30000;

@Injectable({ providedIn: 'root' })
export class BacktestService {
  private readonly apiService = inject(ApiService);

  // Answers already loaded in this session. The result only changes with a manual rebuild of
  // the data, so going back to the page shows it at once instead of asking again.
  private summary: Observable<BacktestResponse> | null = null;
  private readonly details = new Map<RatioKey, Observable<BacktestResponse>>();

  /** Every ratio by year, without the companies in each quintile. */
  getSummary(): Observable<BacktestResponse> {
    if (this.summary === null) {
      this.summary = this.remembered('/backtest');
    }
    return this.summary;
  }

  /** One ratio with the companies of every quintile. */
  getRatio(ratio: RatioKey): Observable<BacktestResponse> {
    let request = this.details.get(ratio);
    if (request === undefined) {
      request = this.remembered(`/backtest/${encodeURIComponent(ratio)}`);
      this.details.set(ratio, request);
    }
    return request;
  }

  // A request nobody waits for any more is cancelled (refCount), and after a failure the next
  // subscriber asks the API again; only a completed answer is replayed.
  private remembered(path: string): Observable<BacktestResponse> {
    return this.apiService
      .get<BacktestResponse>(path, BACKTEST_TIMEOUT_MS)
      .pipe(shareReplay({ bufferSize: 1, refCount: true }));
  }
}
