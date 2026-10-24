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
import { AnnualMetric, AnnualPeriod, FilingValue, Price, Stock } from '../../models/stock';
import { StockService } from '../../services/stock.service';
import { PriceChart } from './price-chart';

@Component({
  selector: 'app-stock-details',
  imports: [RouterLink, PriceChart],
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
  readonly hasUnverifiedFiscalYears = computed(() =>
    this.annualFinancials().some((period) => period.fiscal_year == null),
  );
  readonly missingAnnualYears = computed(() => {
    if (this.hasUnverifiedFiscalYears()) return [];
    const reportedYears = new Set(this.annualFinancials().map((period) => period.fiscal_year));
    const expectedYears = Array.from({ length: 10 }, (_, index) => 2015 + index);
    return expectedYears.filter((year) => !reportedYears.has(year));
  });
  readonly stockLoadState = signal<LoadState>({ status: 'idle' });
  readonly pricesLoadState = signal<LoadState>({ status: 'idle' });
  readonly financialsLoadState = signal<LoadState>({ status: 'idle' });
  readonly metrics = [
    { key: 'revenue', label: 'Revenue' },
    { key: 'net_income', label: 'Net income' },
    { key: 'eps_diluted', label: 'EPS' },
    { key: 'equity', label: 'Parent equity at period end' },
    { key: 'operating_cash_flow', label: 'Operating cash flow' },
  ];

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

  isSelectedSource(metric: AnnualMetric, variant: FilingValue): boolean {
    if (metric.status !== 'ready') return false;
    return (metric.selected_sources ?? []).some(
      (source) =>
        source.source_cik === variant.source_cik &&
        source.taxonomy === variant.taxonomy &&
        source.tag === variant.tag &&
        source.unit === variant.unit &&
        source.accession === variant.accession &&
        source.period_start === variant.period_start &&
        source.period_end === variant.period_end &&
        source.statement_column === variant.statement_column &&
        source.statement_label === variant.statement_label,
    );
  }

  formatValue(value: string | null): string {
    if (value === null) return 'Not available';
    // Format decimal strings without converting financial values to floating point.
    const [integer, decimal = ''] = value.split('.');
    const groupedInteger = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    const significantDecimals = decimal.replace(/0+$/, '');
    return significantDecimals ? `${groupedInteger}.${significantDecimals}` : groupedInteger;
  }
}
