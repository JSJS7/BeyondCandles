import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Stock } from '../../models/stock';
import { LoadState } from '../../models/load-state';
import { StockService } from '../../services/stock.service';

@Component({
  selector: 'app-topbar',
  imports: [],
  templateUrl: './topbar.html',
  styleUrl: './topbar.css',
})
export class TopbarComponent implements OnInit {
  private readonly stockService = inject(StockService);
  private readonly router = inject(Router);

  readonly searchQuery = signal('');
  readonly stocks = signal<Stock[]>([]);
  readonly stocksLoadState = signal<LoadState>({
    status: 'idle',
  });

  readonly filteredStocks = computed(() => {
    const query = this.searchQuery().trim().toLowerCase();

    if (!query) {
      return [];
    }

    return this.stocks().filter((stock) => {
      return (
        stock.ticker.toLowerCase().includes(query) ||
        stock.name.toLowerCase().includes(query)
      );
    });
  });

  ngOnInit(): void {
    this.loadStocks();
  }

  loadStocks(): void {
    this.stocksLoadState.set({
      status: 'loading',
    });

    this.stockService.getStocks().subscribe({
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
          message: 'Failed to load the stock list.',
        });
      },
    });
  }

  onSearchInput(query: string): void {
    this.searchQuery.set(query);
  }

  selectStock(stock: Stock): void {
    this.searchQuery.set('');
    void this.router.navigate(['/stocks', stock.ticker]);
  }
}
