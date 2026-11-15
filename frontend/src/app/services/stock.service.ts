import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { AnnualRatios } from '../models/screener';
import { AnnualPeriod, Price, Stock } from '../models/stock';
import { ApiService } from './api.service';

@Injectable({ providedIn: 'root' })
export class StockService {
  private readonly apiService = inject(ApiService);

  getStocks(): Observable<Stock[]> {
    return this.apiService.get<Stock[]>('/stocks');
  }

  getStock(ticker: string): Observable<Stock> {
    return this.apiService.get<Stock>(`/stocks/${encodeURIComponent(ticker)}`);
  }

  getPrices(ticker: string): Observable<Price[]> {
    return this.apiService.get<Price[]>(`/stocks/${encodeURIComponent(ticker)}/prices`);
  }

  getAnnualFinancials(ticker: string): Observable<AnnualPeriod[]> {
    return this.apiService.get<AnnualPeriod[]>(
      `/stocks/${encodeURIComponent(ticker)}/financials/annual`,
    );
  }

  getAnnualRatios(ticker: string): Observable<AnnualRatios[]> {
    return this.apiService.get<AnnualRatios[]>(
      `/stocks/${encodeURIComponent(ticker)}/ratios/annual`,
    );
  }
}
