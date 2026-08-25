import { Component, output } from '@angular/core';

@Component({
  selector: 'app-sidebar',
  imports: [],
  templateUrl: './sidebar.html',
  styleUrl: './sidebar.css',
})
export class SidebarComponent {
  readonly clearSearchRequested = output<void>();

  clearSearch(): void {
    this.clearSearchRequested.emit();
  }
}