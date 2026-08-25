import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Stock } from '../../models/stock';
import { StockService } from '../../services/stock.service';
import { SidebarComponent } from '../sidebar/sidebar';
import { TopbarComponent } from '../topbar/topbar';

@Component({
  selector: 'app-layout',
  imports: [RouterOutlet, SidebarComponent, TopbarComponent],
  templateUrl: './layout.html',
  styleUrl: './layout.css',
})
export class LayoutComponent implements OnInit {
  private readonly stockService = inject(StockService);

  readonly stocks = signal<Stock[]>([]);
  readonly searchQuery = signal('');

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
    this.stockService.getStocks().subscribe({
      next: (stocks) => {
        this.stocks.set(stocks);
      },
      error: (error: unknown) => {
        console.error('Nie udało się pobrać listy spółek.', error);
      }
    });
  }

  onSearchChange(query: string): void {
    this.searchQuery.set(query);
  }

  clearSearch(): void {
    this.searchQuery.set('');
  }
}