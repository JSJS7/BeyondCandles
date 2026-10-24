import { Component, inject, signal, OnInit } from '@angular/core';
import { HealthService } from '../../services/health.service';

@Component({
  imports: [],
  selector: 'app-dashboard',
  styleUrl: './dashboard.css',
  templateUrl: './dashboard.html',
})
export class Dashboard implements OnInit {
  private readonly healthService = inject(HealthService);

  readonly backendStatus = signal('Checking backend status...');

  ngOnInit(): void {
    this.healthService.getHealth().subscribe({
      next: (response) => {
        this.backendStatus.set(
          response.status === 'ok'
            ? 'Backend is running'
            : 'Backend returned an unexpected response',
        );
      },
      error: () => {
        this.backendStatus.set('Backend is unavailable');
      },
    });
  }
}
