import { Component, ElementRef, signal, viewChild } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Icon } from '../../shared/icon/icon';
import { slideInFromLeft, slideOutToLeft } from '../../shared/slide';
import { Sidebar } from '../sidebar/sidebar';
import { Topbar } from '../topbar/topbar';

// The phone layout, as in the style sheets (styles.css): a narrow window, or a low touch screen
// such as a phone turned sideways. Outside it the sidebar replaces the menu.
const PHONE_LAYOUT_QUERY = '(max-width: 767px), (max-height: 500px) and (pointer: coarse)';
const MENU_CLOSING_CLASS = 'menu-closing';

@Component({
  selector: 'app-layout',
  imports: [RouterOutlet, Sidebar, Topbar, Icon],
  templateUrl: './layout.html',
  styleUrl: './layout.css',
  host: { '(window:resize)': 'onViewportResize()' },
})
export class Layout {
  private readonly mobileMenu = viewChild<ElementRef<HTMLDialogElement>>('mobileMenu');
  private menuOpener: HTMLElement | null = null;
  private menuClosing = false;
  readonly menuOpen = signal(false);

  openMenu(opener: HTMLElement): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog || dialog.open) {
      return;
    }
    this.menuOpener = opener;
    dialog.showModal();
    slideInFromLeft(dialog);
    this.menuOpen.set(true);
  }

  /** The menu slides out first and closes at the end, so a second request meanwhile waits for it. */
  closeMenu(): void {
    const dialog = this.mobileMenu()?.nativeElement;
    if (!dialog?.open || this.menuClosing) {
      return;
    }
    this.menuClosing = true;
    // The class fades the dimmed page while the menu slides out.
    dialog.classList.add(MENU_CLOSING_CLASS);
    slideOutToLeft(dialog, () => {
      // The browser may have closed the menu meanwhile (a second Escape closes it at once), and
      // the reader may have opened it again: then this slide is over and must not close it.
      if (!this.menuClosing) {
        return;
      }
      dialog.close();
      this.onMenuClosed();
    });
  }

  /** Escape would close the menu at once; it slides out like the other ways of closing it. */
  onMenuCancel(event: Event): void {
    event.preventDefault();
    this.closeMenu();
  }

  /** Called however the menu closed: by its slide, or by the browser at once. */
  onMenuClosed(): void {
    this.menuClosing = false;
    this.mobileMenu()?.nativeElement.classList.remove(MENU_CLOSING_CLASS);
    this.menuOpen.set(false);
    this.menuOpener?.focus();
    this.menuOpener = null;
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
    if (!isPhoneLayout()) {
      this.closeMenu();
    }
  }
}

// Test environments without layout have no matchMedia; they count as a desktop window.
function isPhoneLayout(): boolean {
  if (typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia(PHONE_LAYOUT_QUERY).matches;
}
