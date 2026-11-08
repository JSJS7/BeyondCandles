import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ScreenerResponse } from '../models/screener';
import { ApiService } from './api.service';

@Injectable({ providedIn: 'root' })
export class ScreenerService {
  private readonly apiService = inject(ApiService);

  getScreener(valuationDate: string): Observable<ScreenerResponse> {
    return this.apiService.get<ScreenerResponse>(
      `/screener?date=${encodeURIComponent(valuationDate)}`,
    );
  }
}
