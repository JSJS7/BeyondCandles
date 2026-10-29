import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  catchError,
  distinctUntilChanged,
  EMPTY,
  exhaustMap,
  map,
  merge,
  Observable,
  startWith,
  Subject,
  switchMap,
  tap,
} from 'rxjs';
import { LoadState } from '../../models/load-state';
import { AnnualPeriod, Price, Stock } from '../../models/stock';
import { StockService } from '../../services/stock.service';
import { AnnualFinancials } from './annual-financials';
import { PriceChart } from './price-chart';

@Component({
  selector: 'app-stock-details',
  imports: [RouterLink, PriceChart, AnnualFinancials],
  templateUrl: './stock-details.html',
  styleUrl: './stock-details.css',
})
export class StockDetailsComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly stockService = inject(StockService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly stockRetry = new Subject<void>();
  private readonly pricesRetry = new Subject<void>();
  private readonly financialsRetry = new Subject<void>();

  readonly ticker = signal('Unknown');
  readonly stock = signal<Stock | null>(null);
  readonly exchangeName = computed(() => {
    const exchange = this.stock()?.exchange ?? '';
    // Preserve source codes in the API; translate them only for presentation.
    const exchangeLabels: Record<string, string> = {
      NMS: 'NASDAQ',
      NYQ: 'NYSE',
    };
    return exchangeLabels[exchange] ?? exchange;
  });
  readonly prices = signal<Price[]>([]);
  readonly annualFinancials = signal<AnnualPeriod[]>([]);
  readonly stockLoadState = signal<LoadState>({ status: 'idle' });
  readonly pricesLoadState = signal<LoadState>({ status: 'idle' });
  readonly financialsLoadState = signal<LoadState>({ status: 'idle' });

  ngOnInit(): void {
    this.route.paramMap
      .pipe(
        map((params) => params.get('ticker') ?? 'Unknown'),
        distinctUntilChanged(),
        tap((ticker) => {
          this.ticker.set(ticker);
          this.stock.set(null);
          this.prices.set([]);
          this.annualFinancials.set([]);
        }),
        switchMap((ticker) =>
          merge(
            // Load each section once, then accept retries without overlapping requests.
            this.stockRetry.pipe(
              startWith(undefined),
              exhaustMap(() => this.loadStock(ticker)),
            ),
            this.pricesRetry.pipe(
              startWith(undefined),
              exhaustMap(() => this.loadPrices(ticker)),
            ),
            this.financialsRetry.pipe(
              startWith(undefined),
              exhaustMap(() => this.loadFinancials(ticker)),
            ),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }

  retryStock(): void {
    this.stockRetry.next();
  }

  retryPrices(): void {
    this.pricesRetry.next();
  }

  retryFinancials(): void {
    this.financialsRetry.next();
  }

  private loadStock(ticker: string): Observable<Stock> {
    this.stockLoadState.set({ status: 'loading' });
    return this.stockService.getStock(ticker).pipe(
      tap((stock) => {
        this.stock.set(stock);
        this.stockLoadState.set({ status: 'ready' });
      }),
      catchError((error: unknown) => {
        const message =
          error instanceof HttpErrorResponse && error.status === 404
            ? `Stock "${ticker}" was not found.`
            : 'Failed to load stock details.';
        this.stockLoadState.set({ status: 'error', message });
        return EMPTY;
      }),
    );
  }

  private loadPrices(ticker: string): Observable<Price[]> {
    this.pricesLoadState.set({ status: 'loading' });
    return this.stockService.getPrices(ticker).pipe(
      tap((prices) => {
        this.prices.set(prices);
        this.pricesLoadState.set({ status: 'ready' });
      }),
      catchError(() => {
        this.pricesLoadState.set({
          status: 'error',
          message: 'Failed to load price history.',
        });
        return EMPTY;
      }),
    );
  }

  private loadFinancials(ticker: string): Observable<AnnualPeriod[]> {
    this.financialsLoadState.set({ status: 'loading' });
    return this.stockService.getAnnualFinancials(ticker).pipe(
      tap((financials) => {
        this.annualFinancials.set(financials);
        this.financialsLoadState.set({ status: 'ready' });
      }),
      catchError(() => {
        this.financialsLoadState.set({
          status: 'error',
          message: 'Failed to load annual financials.',
        });
        return EMPTY;
      }),
    );
  }
}
