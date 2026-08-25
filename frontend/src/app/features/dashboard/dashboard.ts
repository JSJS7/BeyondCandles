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

  readonly backendStatus = signal('Sprawdzanie...');

  ngOnInit(): void {
    this.healthService.getHealth().subscribe({
      next: (response) => {
        this.backendStatus.set(response.status === 'ok'
          ? 'Backend działa'
          : 'Backend zwrócił nieoczekiwaną odpowiedź'
        );
      },
      error: () => {
        this.backendStatus.set('Backend jest niedostępny');
      }
    });
  }
}