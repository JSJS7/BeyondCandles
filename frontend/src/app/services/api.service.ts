import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, timeout } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = 'http://127.0.0.1:8000';
  private readonly requestTimeoutMs = 5000;

  get<T>(path: string): Observable<T> {
    return this.http.get<T>(`${this.apiUrl}${path}`).pipe(timeout(this.requestTimeoutMs));
  }
}
