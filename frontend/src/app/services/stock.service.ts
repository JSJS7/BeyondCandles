import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Stock } from '../models/stock';
import { ApiService } from './api.service';

@Injectable({
  providedIn: 'root'
})
export class StockService {
  private readonly apiService = inject(ApiService);

  getStocks(): Observable<Stock[]> {
    return this.apiService.get<Stock[]>('/stocks');
  }
}