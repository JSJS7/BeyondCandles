import { Injectable, inject } from '@angular/core';
import { Observable, shareReplay } from 'rxjs';
import { ScreenerResponse } from '../models/screener';
import { ApiService } from './api.service';

/** Enough for going back and forth between a few dates; each answer is about 140 kB. */
const REMEMBERED_DATES = 10;

@Injectable({ providedIn: 'root' })
export class ScreenerService {
  private readonly apiService = inject(ApiService);

  /**
   * Answers already loaded in this session, oldest first. The data only change with a manual
   * rebuild, so going back to the screener shows the table at once instead of asking again.
   */
  private readonly responses = new Map<string, Observable<ScreenerResponse>>();

  getScreener(valuationDate: string): Observable<ScreenerResponse> {
    const remembered = this.responses.get(valuationDate);
    if (remembered) {
      return remembered;
    }

    // A request nobody waits for any more is cancelled (refCount), and after a failure the next
    // subscriber asks the API again; only a completed answer is replayed.
    const request = this.apiService
      .get<ScreenerResponse>(`/screener?date=${encodeURIComponent(valuationDate)}`)
      .pipe(shareReplay({ bufferSize: 1, refCount: true }));
    this.responses.set(valuationDate, request);

    if (this.responses.size > REMEMBERED_DATES) {
      const oldestDate = this.responses.keys().next().value;
      if (oldestDate !== undefined) {
        this.responses.delete(oldestDate);
      }
    }
    return request;
  }
}
