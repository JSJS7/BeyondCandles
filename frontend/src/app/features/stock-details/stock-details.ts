import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { LoadState } from '../../models/load-state';
import { Stock } from '../../models/stock';
import { StockService } from '../../services/stock.service';
import { catchError, distinctUntilChanged, EMPTY, map, switchMap, tap } from 'rxjs';

@Component({
  selector: 'app-stock-details',
  imports: [RouterLink],
  templateUrl: './stock-details.html',
  styleUrl: './stock-details.css',
})
export class StockDetailsComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly stockService = inject(StockService);

  readonly ticker = signal('Unknown');

  readonly stock = signal<Stock | null>(null);
  readonly stockLoadState = signal<LoadState>({ status: 'idle' });

  ngOnInit(): void {
    this.route.paramMap
      .pipe(
        map((params) => params.get('ticker') ?? 'Unknown'),
        distinctUntilChanged(),
        tap((ticker) => {
          this.ticker.set(ticker);
          this.stock.set(null);
          this.stockLoadState.set({ status: 'loading' });
        }),
        switchMap((ticker) =>
          this.stockService.getStock(ticker).pipe(
            catchError((error: unknown) => {
              const message =
                error instanceof HttpErrorResponse && error.status === 404
                  ? `Stock "${ticker}" was not found.`
                  : 'Failed to load stock details.';

              console.error(message, error);
              this.stockLoadState.set({ status: 'error', message });

              return EMPTY;
            }),
          ),
        ),
      )
      .subscribe((stock) => {
        this.stock.set(stock);
        this.stockLoadState.set({ status: 'ready' });
      });
  }
}
