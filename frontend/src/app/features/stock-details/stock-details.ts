import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Title } from '@angular/platform-browser';
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
import {
  FIRST_PERIOD_YEAR,
  FIRST_PRICE_DATE,
  LAST_PERIOD_YEAR,
  LAST_PRICE_DATE,
} from '../../shared/data-window';
import { formatDate } from '../../shared/date-format';
import { exchangeName } from '../../shared/exchange-name';
import { StockService } from '../../services/stock.service';
import { pageTitle } from '../../shared/page-title';
import { StateMessage } from '../../shared/state-message/state-message';
import { AnnualFinancials } from './annual-financials';
import { PriceChart } from './price-chart';
import { PRICE_SERIES_DESCRIPTION } from './price-chart-data';

@Component({
  selector: 'app-stock-details',
  imports: [RouterLink, PriceChart, AnnualFinancials, StateMessage],
  templateUrl: './stock-details.html',
  styleUrl: './stock-details.css',
})
export class StockDetails implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly stockService = inject(StockService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly title = inject(Title);
  private readonly stockRetry = new Subject<void>();
  private readonly pricesRetry = new Subject<void>();
  private readonly financialsRetry = new Subject<void>();

  readonly priceSeriesDescription = PRICE_SERIES_DESCRIPTION;
  readonly priceRangeText = `${formatDate(FIRST_PRICE_DATE)} to ${formatDate(LAST_PRICE_DATE)}`;
  readonly periodYearsText = `${FIRST_PERIOD_YEAR}–${LAST_PERIOD_YEAR}`;
  readonly ticker = signal('Unknown');
  readonly stock = signal<Stock | null>(null);
  readonly exchangeName = computed(() => exchangeName(this.stock()?.exchange ?? ''));
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
        // The route gave the tab the ticker; the name is what the page heading shows.
        this.title.setTitle(pageTitle(`${stock.name} (${stock.ticker})`));
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
