import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, timeout } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private readonly http = inject(HttpClient);
  // Relative, so that a phone in the same network reaches the API through the dev server
  // (`proxy.conf.json` forwards /api to the API on 127.0.0.1:8000).
  private readonly apiUrl = '/api';
  private readonly requestTimeoutMs = 5000;

  /** A slower answer, such as the backtest computed on its first request, can allow more time. */
  get<T>(path: string, timeoutMs = this.requestTimeoutMs): Observable<T> {
    return this.http.get<T>(`${this.apiUrl}${path}`).pipe(timeout(timeoutMs));
  }
}
