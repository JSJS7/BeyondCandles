import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { Sidebar } from '../sidebar/sidebar';
import { Topbar } from '../topbar/topbar';

// The same width as in the style sheets: from here on the sidebar is shown and the menu is not.
const DESKTOP_MIN_WIDTH = 768;

@Component({
  selector: 'app-layout',
  imports: [RouterOutlet, Sidebar, Topbar],
  templateUrl: './layout.html',
  styleUrl: './layout.css',
  host: { '(window:resize)': 'onViewportResize()' },
})
export class Layout {
  private readonly mobileMenu = viewChild<ElementRef<HTMLDialogElement>>('mobileMenu');
  private readonly main = viewChild<ElementRef<HTMLElement>>('main');
  private menuOpener: HTMLElement | null = null;
  private currentPath: string | null = null;
  readonly menuOpen = signal(false);

  constructor() {
    // W34: pages scroll inside <main>, not the window, so the router cannot reset the place.
    // A new page starts at the top. A change of the query string alone keeps the place: the
    // screener writes its state there on every keystroke.
    inject(Router)
      .events.pipe(
        filter((event) => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe((event) => {
        const path = event.urlAfterRedirects.split(/[?#]/)[0];
        const pathChanged = this.currentPath !== null && path !== this.currentPath;
        const main = this.main()?.nativeElement;
        if (pathChanged && main) {
          main.scrollTop = 0;
        }
        this.currentPath = path;
      });
  }

  openMenu(opener: HTMLElement): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog || dialog.open) {
      return;
    }
    this.menuOpener = opener;
    dialog.showModal();
    this.menuOpen.set(true);
  }

  closeMenu(): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (dialog?.open) {
      dialog.close();
    }
    this.onMenuClosed();
  }

  onMenuClosed(): void {
    this.menuOpen.set(false);
    this.menuOpener?.focus();
    this.menuOpener = null;
  }

  /** Tab and Shift+Tab go round inside the open menu instead of to the page behind it. */
  trapMenuFocus(event: KeyboardEvent): void {
    if (event.key !== 'Tab') {
      return;
    }
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog?.open) {
      return;
    }

    const controls = dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), [tabindex="0"]',
    );
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!first || !last) {
      return;
    }
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /** A click on the dimmed page beside the menu closes it; a click inside does not. */
  onBackdropClick(event: MouseEvent): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog || event.target !== dialog) {
      return;
    }
    const bounds = dialog.getBoundingClientRect();
    const outsideHorizontally = event.clientX < bounds.left || event.clientX > bounds.right;
    const outsideVertically = event.clientY < bounds.top || event.clientY > bounds.bottom;
    if (outsideHorizontally || outsideVertically) {
      this.closeMenu();
    }
  }

  onViewportResize(): void {
    if (window.innerWidth >= DESKTOP_MIN_WIDTH) {
      this.closeMenu();
    }
  }
}
