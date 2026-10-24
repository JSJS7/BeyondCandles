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
import { Stock } from '../../models/stock';
import { LoadState } from '../../models/load-state';
import { StockService } from '../../services/stock.service';
import { ThemeService } from '../../services/theme.service';

@Component({
  selector: 'app-topbar',
  imports: [],
  templateUrl: './topbar.html',
  styleUrl: './topbar.css',
})
export class TopbarComponent implements OnInit {
  readonly menuOpen = input(false);
  readonly menuRequested = output<HTMLElement>();
  readonly theme = inject(ThemeService);
  private readonly stockService = inject(StockService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  readonly searchQuery = signal('');
  readonly searchResultsOpen = signal(false);
  readonly stocks = signal<Stock[]>([]);
  readonly stocksLoadState = signal<LoadState>({
    status: 'idle',
  });

  readonly filteredStocks = computed(() => {
    const query = this.searchQuery().trim().toLowerCase();

    if (!query) {
      return [];
    }

    const matches = this.stocks().filter((stock) => {
      return stock.ticker.toLowerCase().includes(query) || stock.name.toLowerCase().includes(query);
    });
    return matches.slice(0, 10);
  });

  ngOnInit(): void {
    this.loadStocks();
  }

  loadStocks(): void {
    if (this.stocksLoadState().status === 'loading') {
      return;
    }

    this.stocksLoadState.set({
      status: 'loading',
    });

    this.stockService
      .getStocks()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (stocks) => {
          this.stocks.set(stocks);
          this.stocksLoadState.set({
            status: 'ready',
          });
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
  }

  dismissSearchResults(): void {
    this.searchResultsOpen.set(false);
  }

  selectStock(stock: Stock): void {
    this.searchQuery.set('');
    this.dismissSearchResults();
    void this.router.navigate(['/stocks', stock.ticker]);
  }
}
