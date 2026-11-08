import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, convertToParamMap, Router, RouterLink } from '@angular/router';
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
import { LoadState } from '../../models/load-state';
import { ScreenerResponse } from '../../models/screener';
import { ScreenerService } from '../../services/screener.service';
import { formatDate } from '../../shared/date-format';
import { CriteriaEditor, CriterionView } from './criteria-editor';
import { OptionPicker, OptionToggle, PickerOption } from './option-picker';
import {
  cellLabels,
  cellText,
  ColumnKey,
  columnSummary,
  findColumn,
  formatValue,
  SCREENER_COLUMNS,
  ScreenerColumn,
  visibleColumns,
} from './screener-columns';
import { screenerCsv } from './screener-csv';
import {
  CRITERION_COLUMNS,
  CriterionInput,
  criterionStats,
  criterionStatus,
  criterionUnit,
  filterBySearch,
  filterBySector,
  filterRows,
  sectorSummary,
} from './screener-filters';
import {
  addressKey,
  EARLIEST_VALUATION_DATE,
  isValidValuationDate,
  LATEST_VALUATION_DATE,
  parseScreenerQuery,
  ScreenerQuery,
  screenerQueryParams,
} from './screener-query';
import {
  countMissing,
  DEFAULT_SORT,
  nextSort,
  SortKey,
  sortKeyLabel,
  sortRows,
} from './screener-sort';

/** Everything on the page that the reader changes, apart from the valuation date. */
type PageState = Omit<ScreenerQuery, 'date'>;

function pageState(query: ScreenerQuery): PageState {
  return {
    sectors: query.sectors,
    excludeSectors: query.excludeSectors,
    search: query.search,
    sort: query.sort,
    criteria: query.criteria,
    hiddenColumns: query.hiddenColumns,
  };
}

@Component({
  selector: 'app-screener',
  imports: [RouterLink, OptionPicker, CriteriaEditor],
  templateUrl: './screener.html',
  styleUrl: './screener.css',
})
export class Screener implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly screenerService = inject(ScreenerService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly retryRequests = new Subject<void>();
  // The address that this page wrote last. Any other address came from outside, such as the
  // Screener link in the menu, and replaces the page state.
  private writtenAddress: string | null = null;

  readonly earliestDate = EARLIEST_VALUATION_DATE;
  readonly latestDate = LATEST_VALUATION_DATE;
  readonly dateRangeLabel = `${formatDate(EARLIEST_VALUATION_DATE)} to ${formatDate(LATEST_VALUATION_DATE)}`;
  readonly cellText = cellText;
  readonly cellLabels = cellLabels;

  // The valuation date is read from the address, because a new date loads new data.
  readonly date = toSignal(
    this.route.queryParamMap.pipe(map((params) => parseScreenerQuery(params).date)),
    { requireSync: true },
  );

  // The rest of the state lives here and is written to the address after every change, so that
  // Back from a company page restores it. It is not read back from the address: the text of an
  // input would return a moment after each keystroke, and a quick second click would see the
  // state before the first one.
  readonly state = signal<PageState>(
    pageState(parseScreenerQuery(this.route.snapshot.queryParamMap)),
  );

  readonly result = signal<ScreenerResponse | null>(null);
  readonly loadState = signal<LoadState>({ status: 'idle' });
  readonly dateInvalid = signal(false);

  /** Every sector with its number of companies on the valuation date. */
  readonly sectorOptions = computed(() => {
    const counts = new Map<string, number>();
    for (const row of this.result()?.rows ?? []) {
      if (row.sector !== null) {
        counts.set(row.sector, (counts.get(row.sector) ?? 0) + 1);
      }
    }
    // Kept in the list while loading and for an unknown address, so the control tells the truth.
    for (const selected of this.state().sectors) {
      if (!counts.has(selected)) {
        counts.set(selected, 0);
      }
    }
    const names = [...counts.keys()].sort((first, second) => first.localeCompare(second, 'en'));
    return names.map((name): PickerOption => ({ key: name, label: name, count: counts.get(name) }));
  });
  readonly sectorSummary = computed(() =>
    sectorSummary(this.state().sectors, this.state().excludeSectors),
  );

  // The checkboxes of the column control tick the columns that are shown.
  readonly columnOptions: PickerOption[] = SCREENER_COLUMNS.map((column) => ({
    key: column.key,
    label: column.label,
  }));
  readonly shownColumns = computed(() => visibleColumns(this.state().hiddenColumns));
  readonly shownColumnKeys = computed(() => this.shownColumns().map((column) => column.key));
  readonly columnSummary = computed(() => columnSummary(this.state().hiddenColumns));

  // The rows pass the filters in this order; each step is kept for its own counts.
  readonly sectorRows = computed(() => {
    const state = this.state();
    return filterBySector(this.result()?.rows ?? [], state.sectors, state.excludeSectors);
  });
  readonly matchingRows = computed(() => filterRows(this.sectorRows(), this.state().criteria));
  readonly searchedRows = computed(() => filterBySearch(this.matchingRows(), this.state().search));
  readonly visibleRows = computed(() => sortRows(this.searchedRows(), this.state().sort));

  readonly totalCount = computed(() => this.result()?.rows.length ?? 0);
  readonly sortedColumnLabel = computed(() => sortKeyLabel(this.state().sort.key));
  readonly missingCount = computed(() => countMissing(this.searchedRows(), this.state().sort.key));

  readonly availableCriteria = computed(() =>
    CRITERION_COLUMNS.filter((column) => {
      const isAdded = this.state().criteria.some((added) => added.key === column.key);
      return !isAdded;
    }),
  );
  readonly filtersActive = computed(() => {
    const state = this.state();
    const hasActiveCriterion = state.criteria.some(
      (criterion) => criterionStatus(criterion).active,
    );
    return state.sectors.length > 0 || hasActiveCriterion || state.search.trim() !== '';
  });
  readonly criterionViews = computed(() =>
    this.state().criteria.map((criterion) => this.criterionView(criterion)),
  );

  readonly valuationDateLabel = computed(() => {
    const result = this.result();
    return result ? formatDate(result.valuation_date) : '';
  });

  /** The session behind the prices, when the chosen day itself had no trading. */
  readonly earlierSessionLabel = computed(() => {
    const result = this.result();
    if (!result) {
      return null;
    }

    let latestSession: string | null = null;
    for (const row of result.rows) {
      const session = row.latest?.price_date ?? null;
      if (session !== null && (latestSession === null || session > latestSession)) {
        latestSession = session;
      }
    }
    if (latestSession === null || latestSession === result.valuation_date) {
      return null;
    }
    return formatDate(latestSession);
  });

  ngOnInit(): void {
    this.route.queryParamMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      if (addressKey(params) !== this.writtenAddress) {
        this.state.set(pageState(parseScreenerQuery(params)));
      }
    });

    this.route.queryParamMap
      .pipe(
        map((params) => parseScreenerQuery(params).date),
        distinctUntilChanged(),
        // The old result disappears at once: it does not answer the new date.
        tap(() => this.result.set(null)),
        switchMap((date) =>
          // Load once, then accept retries without overlapping requests.
          this.retryRequests.pipe(
            startWith(undefined),
            exhaustMap(() => this.load(date)),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }

  retry(): void {
    this.retryRequests.next();
  }

  /** An incomplete or out-of-range date sends no request; the table keeps naming its own date. */
  onDateChange(value: string): void {
    const valid = isValidValuationDate(value);
    this.dateInvalid.set(!valid);
    if (valid) {
      this.writeAddress(value);
    }
  }

  onSectorToggled(change: OptionToggle): void {
    const others = this.state().sectors.filter((sector) => sector !== change.key);
    const sectors = change.chosen ? [...others, change.key] : others;
    this.changeState({ sectors });
  }

  onExcludeSectorsChange(exclude: boolean): void {
    this.changeState({ excludeSectors: exclude });
  }

  clearSectors(): void {
    this.changeState({ sectors: [], excludeSectors: false });
  }

  /** A ticked column is shown; the state keeps the hidden ones, in table order. */
  onColumnToggled(change: OptionToggle): void {
    const hiddenBefore = this.state().hiddenColumns;
    const hiddenColumns = SCREENER_COLUMNS.map((column) => column.key).filter((key) => {
      if (key === change.key) {
        return !change.chosen;
      }
      return hiddenBefore.includes(key);
    });
    const sortKey = this.state().sort.key;
    // Hiding the column that orders the table returns it to the default order.
    const sortIsHidden = sortKey !== 'company' && hiddenColumns.includes(sortKey);
    if (sortIsHidden) {
      this.changeState({ hiddenColumns, sort: DEFAULT_SORT });
    } else {
      this.changeState({ hiddenColumns });
    }
  }

  showAllColumns(): void {
    this.changeState({ hiddenColumns: [] });
  }

  onSearchInput(text: string): void {
    this.changeState({ search: text });
  }

  clearSearch(): void {
    this.changeState({ search: '' });
  }

  addCriterion(key: ColumnKey): void {
    const isAvailable = this.availableCriteria().some((column) => column.key === key);
    if (!isAvailable) {
      return;
    }
    const criteria = [...this.state().criteria, { key, min: '', max: '' }];
    this.changeState({ criteria });
  }

  setBound(key: ColumnKey, bound: 'min' | 'max', text: string): void {
    const criteria = this.state().criteria.map((criterion) => {
      if (criterion.key !== key) {
        return criterion;
      }
      return { ...criterion, [bound]: text };
    });
    this.changeState({ criteria });
  }

  removeCriterion(key: ColumnKey): void {
    const criteria = this.state().criteria.filter((criterion) => criterion.key !== key);
    this.changeState({ criteria });
  }

  clearCriteria(): void {
    this.changeState({ criteria: [] });
  }

  toggleSort(key: SortKey): void {
    this.changeState({ sort: nextSort(this.state().sort, key) });
  }

  ariaSort(key: SortKey): 'ascending' | 'descending' | 'none' {
    const sort = this.state().sort;
    if (sort.key !== key) {
      return 'none';
    }
    return sort.direction === 'asc' ? 'ascending' : 'descending';
  }

  sortIndicator(key: SortKey): string {
    const sort = this.state().sort;
    if (sort.key !== key) {
      return '';
    }
    return sort.direction === 'asc' ? '▲' : '▼';
  }

  /** The rows as shown, in the shown order, saved as a file by the browser. */
  downloadCsv(): void {
    const result = this.result();
    if (!result) {
      return;
    }

    const csv = screenerCsv(this.visibleRows(), result.valuation_date);
    // The byte order mark tells spreadsheet programs that the file is UTF-8.
    const byteOrderMark = String.fromCharCode(0xfeff);
    const file = new Blob([byteOrderMark, csv], { type: 'text/csv;charset=utf-8' });
    const address = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = address;
    link.download = `screener-${result.valuation_date}.csv`;
    link.click();
    URL.revokeObjectURL(address);
  }

  /** What one criterion card shows: its label, unit, input problems and who it leaves out. */
  private criterionView(criterion: CriterionInput): CriterionView {
    const status = criterionStatus(criterion);
    const column = findColumn(criterion.key);
    return {
      key: criterion.key,
      label: column?.label ?? criterion.key,
      unit: criterionUnit(criterion.key),
      min: criterion.min,
      max: criterion.max,
      minInvalid: status.minInvalid,
      maxInvalid: status.maxInvalid,
      reversed: status.reversed,
      // Companies without a value are left out only while the criterion limits something.
      leftOut: status.active ? countMissing(this.sectorRows(), criterion.key) : 0,
      stats: column ? this.statsView(column) : null,
    };
  }

  private statsView(column: ScreenerColumn): CriterionView['stats'] {
    // Of the chosen sectors, before any criterion, so that a range does not narrow its own hint.
    const stats = criterionStats(this.sectorRows(), column.key);
    if (stats === null) {
      return null;
    }
    return {
      count: stats.count,
      median: formatValue(column, stats.median),
      p10: formatValue(column, stats.p10),
      p90: formatValue(column, stats.p90),
    };
  }

  private changeState(change: Partial<PageState>): void {
    this.state.update((state) => ({ ...state, ...change }));
    this.writeAddress(this.date());
  }

  private writeAddress(date: string): void {
    const params = screenerQueryParams({ date, ...this.state() });
    this.writtenAddress = addressKey(convertToParamMap(params));
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: params,
      replaceUrl: true,
    });
  }

  private load(date: string): Observable<ScreenerResponse> {
    this.loadState.set({ status: 'loading' });
    return this.screenerService.getScreener(date).pipe(
      tap((result) => {
        this.result.set(result);
        this.loadState.set({ status: 'ready' });
      }),
      catchError(() => {
        this.loadState.set({ status: 'error', message: 'Failed to load the screener.' });
        return EMPTY;
      }),
    );
  }
}
