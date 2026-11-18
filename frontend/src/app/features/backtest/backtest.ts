import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import {
  catchError,
  distinctUntilChanged,
  EMPTY,
  exhaustMap,
  map,
  Observable,
  startWith,
  Subject,
  switchMap,
  tap,
} from 'rxjs';
import { BacktestResponse, RatioBacktest } from '../../models/backtest';
import { LoadState } from '../../models/load-state';
import { RatioKey } from '../../models/screener';
import { BacktestService } from '../../services/backtest.service';
import { METHODOLOGY_URL } from '../../shared/links';
import { StateMessage } from '../../shared/state-message/state-message';
import {
  BACKTEST_RATIO_KEYS,
  Costs,
  directionText,
  inScreenerOrder,
  parseBacktestRatio,
  ratioDefinition,
} from './backtest-format';
import { BacktestHoldings } from './backtest-holdings';
import { BacktestSummary } from './backtest-summary';
import { BacktestYears } from './backtest-years';

/** The annual quintile backtest of every screener ratio (DEC-33); the chosen ratio is in the address. */
@Component({
  selector: 'app-backtest',
  imports: [StateMessage, BacktestSummary, BacktestYears, BacktestHoldings],
  templateUrl: './backtest.html',
  styleUrl: './backtest.css',
})
export class Backtest implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly backtestService = inject(BacktestService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly summaryRetry = new Subject<void>();
  private readonly detailRetry = new Subject<void>();

  readonly ratio = toSignal(
    this.route.queryParamMap.pipe(map((params) => parseBacktestRatio(params.get('ratio')))),
    { requireSync: true },
  );
  readonly costs = signal<Costs>('net');

  readonly summary = signal<BacktestResponse | null>(null);
  readonly detail = signal<RatioBacktest | null>(null);
  readonly summaryLoadState = signal<LoadState>({ status: 'idle' });
  readonly detailLoadState = signal<LoadState>({ status: 'idle' });

  readonly ratioOptions = BACKTEST_RATIO_KEYS.map((key) => ({
    key,
    label: ratioDefinition(key).label,
  }));
  readonly orderedRatios = computed(() => inScreenerOrder(this.summary()?.ratios ?? []));
  readonly selectedRatio = computed(() =>
    this.orderedRatios().find((item) => item.ratio === this.ratio()),
  );
  readonly selectedLabel = computed(() => ratioDefinition(this.ratio()).label);
  readonly methodologyUrl = METHODOLOGY_URL;
  readonly directionText = directionText;

  ngOnInit(): void {
    this.summaryRetry
      .pipe(
        startWith(undefined),
        exhaustMap(() => this.loadSummary()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();

    this.route.queryParamMap
      .pipe(
        map((params) => parseBacktestRatio(params.get('ratio'))),
        distinctUntilChanged(),
        // The companies of the old ratio disappear at once: they do not answer the new one.
        tap(() => this.detail.set(null)),
        switchMap((ratio) =>
          this.detailRetry.pipe(
            startWith(undefined),
            exhaustMap(() => this.loadDetail(ratio)),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }

  chooseRatio(text: string): void {
    const ratio = parseBacktestRatio(text);
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { ratio },
      queryParamsHandling: 'merge',
    });
  }

  chooseCosts(costs: Costs): void {
    this.costs.set(costs);
  }

  retrySummary(): void {
    this.summaryRetry.next();
  }

  retryDetail(): void {
    this.detailRetry.next();
  }

  private loadSummary(): Observable<BacktestResponse> {
    this.summaryLoadState.set({ status: 'loading' });
    return this.backtestService.getSummary().pipe(
      tap((response) => {
        this.summary.set(response);
        this.summaryLoadState.set({ status: 'ready' });
      }),
      catchError(() => {
        this.summaryLoadState.set({ status: 'error', message: 'Failed to load the backtest.' });
        return EMPTY;
      }),
    );
  }

  private loadDetail(ratio: RatioKey): Observable<BacktestResponse> {
    this.detailLoadState.set({ status: 'loading' });
    return this.backtestService.getRatio(ratio).pipe(
      tap((response) => {
        this.detail.set(response.ratios[0] ?? null);
        this.detailLoadState.set({ status: 'ready' });
      }),
      catchError(() => {
        this.detailLoadState.set({
          status: 'error',
          message: 'Failed to load the companies of each quintile.',
        });
        return EMPTY;
      }),
    );
  }
}
