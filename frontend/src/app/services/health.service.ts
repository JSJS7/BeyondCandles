import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';

interface HealthResponse {
  status: string;
}

@Injectable({
  providedIn: 'root',
})
export class HealthService {
  private readonly apiService = inject(ApiService);

  getHealth(): Observable<HealthResponse> {
    return this.apiService.get<HealthResponse>('/health');
  }
}
