import {
  Component,
  computed,
  DestroyRef,
  inject,
  input,
  OnInit,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { LoadState } from '../../models/load-state';
import { Stock } from '../../models/stock';
import { exchangeName } from '../../shared/exchange-name';
import { StockService } from '../../services/stock.service';
import { ThemeService } from '../../services/theme.service';
import { matchRank } from '../../shared/search-match';
import { StateMessage } from '../../shared/state-message/state-message';

const MAX_SUGGESTIONS = 10;

@Component({
  selector: 'app-topbar',
  imports: [StateMessage],
  templateUrl: './topbar.html',
  styleUrl: './topbar.css',
  host: { '(document:pointerdown)': 'onDocumentPointerDown($event)' },
})
export class Topbar implements OnInit {
  private readonly stockService = inject(StockService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  readonly menuOpen = input(false);
  readonly menuRequested = output<HTMLElement>();
  readonly theme = inject(ThemeService);
  readonly exchangeLabel = exchangeName;

  readonly searchQuery = signal('');
  readonly searchResultsOpen = signal(false);
  readonly stocks = signal<Stock[]>([]);
  readonly stocksLoadState = signal<LoadState>({ status: 'idle' });
  readonly activeOption = signal(-1);

  readonly filteredStocks = computed(() => {
    const query = this.searchQuery().trim().toLowerCase();
    if (!query) {
      return [];
    }

    const matches: { stock: Stock; rank: number }[] = [];
    for (const stock of this.stocks()) {
      const rank = matchRank(stock.ticker, stock.name, query);
      if (rank !== null) {
        matches.push({ stock, rank });
      }
    }
    // The sort is stable, so equal ranks keep the catalogue order (alphabetical by ticker).
    matches.sort((first, second) => first.rank - second.rank);
    return matches.slice(0, MAX_SUGGESTIONS).map((match) => match.stock);
  });

  readonly optionsVisible = computed(() => {
    const isReady = this.stocksLoadState().status === 'ready';
    return this.searchResultsOpen() && isReady && this.filteredStocks().length > 0;
  });

  readonly activeOptionId = computed(() => {
    const active = this.activeOption();
    if (!this.optionsVisible() || active < 0) {
      return null;
    }
    return this.optionId(active);
  });

  ngOnInit(): void {
    this.loadStocks();
  }

  loadStocks(): void {
    if (this.stocksLoadState().status === 'loading') {
      return;
    }
    this.stocksLoadState.set({ status: 'loading' });

    this.stockService
      .getStocks()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (stocks) => {
          this.stocks.set(stocks);
          this.stocksLoadState.set({ status: 'ready' });
        },
        error: (error: unknown) => {
          console.error('Failed to load the stock list.', error);
          this.stocksLoadState.set({
            status: 'error',
            message: 'Companies are temporarily unavailable.',
          });
        },
      });
  }

  onSearchFocus(): void {
    this.searchResultsOpen.set(true);
    if (this.stocksLoadState().status === 'error') {
      this.loadStocks();
    }
  }

  onSearchInput(query: string): void {
    this.searchQuery.set(query);
    this.searchResultsOpen.set(true);
    this.activeOption.set(-1);
  }

  /** Focus stays in the input; the arrow keys only mark an option (aria-activedescendant). */
  onSearchKeydown(event: KeyboardEvent): void {
    const options = this.filteredStocks();

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      // Keeps the caret in place and the page from scrolling, also with no options.
      event.preventDefault();
      if (!options.length) {
        return;
      }
      if (!this.searchResultsOpen()) {
        this.searchResultsOpen.set(true);
        return;
      }
      const step = event.key === 'ArrowDown' ? 1 : -1;
      const current = this.activeOption();
      // From no marked option, Down goes to the first and Up to the last.
      const start = current < 0 && step < 0 ? 0 : current;
      this.activeOption.set((start + step + options.length) % options.length);
    } else if (event.key === 'Enter') {
      if (!this.optionsVisible()) {
        return;
      }
      event.preventDefault();
      this.selectStock(options[Math.max(this.activeOption(), 0)]);
    } else if (event.key === 'Tab') {
      // N38: in the error panel, Tab moves forward to "Try again", so the panel must stay.
      // The options are not Tab stops, and the other panels have nothing to reach.
      const errorShown = this.stocksLoadState().status === 'error';
      const movesToRetryButton = errorShown && !event.shiftKey;
      if (!movesToRetryButton) {
        this.dismissSearchResults();
      }
    }
  }

  /** Tab forward from "Try again" leaves the search; Shift+Tab goes back to the input. */
  onRetryKeydown(event: KeyboardEvent): void {
    if (event.key === 'Tab' && !event.shiftKey) {
      this.dismissSearchResults();
    }
  }

  onDocumentPointerDown(event: Event): void {
    const target = event.target;
    if (target instanceof Element && target.closest('.global-search')) {
      return;
    }
    this.dismissSearchResults();
  }

  optionId(index: number): string {
    return `search-option-${index}`;
  }

  dismissSearchResults(): void {
    this.searchResultsOpen.set(false);
    this.activeOption.set(-1);
  }

  selectStock(stock: Stock): void {
    this.searchQuery.set('');
    this.dismissSearchResults();
    void this.router.navigate(['/stocks', stock.ticker]);
  }
}
