import { Component, input, output } from '@angular/core';
import { Stock } from '../../models/stock';

@Component({
  imports: [],
  selector: 'app-topbar',
  styleUrl: './topbar.css',
  templateUrl: './topbar.html',
})
export class TopbarComponent {
  readonly searchQuery = input('');
  readonly filteredStocks = input<Stock[]>([]);

  readonly searchQueryChanged = output<string>();
  readonly searchCleared = output<void>();

  onSearchInput(query: string): void {
    this.searchQueryChanged.emit(query);
  }

  clearSearch(): void {
    this.searchCleared.emit();
  }
}
