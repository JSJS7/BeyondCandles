import { Component, ElementRef, HostListener, signal, viewChild } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { SidebarComponent } from '../sidebar/sidebar';
import { TopbarComponent } from '../topbar/topbar';

@Component({
  selector: 'app-layout',
  imports: [RouterOutlet, SidebarComponent, TopbarComponent],
  templateUrl: './layout.html',
  styleUrl: './layout.css',
})
export class LayoutComponent {
  private readonly mobileMenu = viewChild<ElementRef<HTMLDialogElement>>('mobileMenu');
  private menuOpener: HTMLElement | null = null;
  readonly menuOpen = signal(false);

  openMenu(opener: HTMLElement): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog || dialog.open) return;
    this.menuOpener = opener;
    dialog.showModal();
    this.menuOpen.set(true);
  }

  closeMenu(): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (dialog?.open) dialog.close();
    this.onMenuClosed();
  }

  onMenuClosed(): void {
    this.menuOpen.set(false);
    this.menuOpener?.focus();
    this.menuOpener = null;
  }

  trapMenuFocus(event: KeyboardEvent): void {
    if (event.key !== 'Tab') return;
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog?.open) return;
    const controls = dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), [tabindex="0"]',
    );
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  onBackdropClick(event: MouseEvent): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog || event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    const outside =
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom;
    if (outside) this.closeMenu();
  }

  @HostListener('window:resize')
  onViewportResize(): void {
    if (window.innerWidth >= 768) this.closeMenu();
  }
}
