import { ViewportScroller } from '@angular/common';
import { inject, Injectable } from '@angular/core';
import { NavigationEnd, NavigationStart, Router } from '@angular/router';

// How long a restored place is held while the page grows and settles, for example while the
// screener loads its data again after Back.
const RESTORE_WAIT_MS = 10_000;
// A reader who moves the page during that time has chosen a place of their own.
const READER_INPUT_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'];

interface ScrollPlace {
  left: number;
  top: number;
}

interface PagePlace {
  window: ScrollPlace;
  /** Places of the areas with their own scrolling (scroll-memory-area.ts), by their keys. */
  areas: Map<string, ScrollPlace>;
}

/**
 * Where each page was left, so that Back and Forward return to the same place.
 *
 * A new page starts at the top (W34). A change of the query string alone keeps the place: the
 * screener writes its state there on every keystroke. The router's own scrolling
 * (withInMemoryScrolling) cannot tell the two apart, restores only the window and does not wait
 * for data, so the places are kept here by the router's navigation ids, which history entries
 * carry.
 */
@Injectable({ providedIn: 'root' })
export class ScrollMemory {
  private readonly router = inject(Router);
  private readonly viewportScroller = inject(ViewportScroller);

  private readonly savedPlaces = new Map<number, PagePlace>();
  private readonly areas = new Map<string, HTMLElement>();
  private pendingAreaPlaces = new Map<string, ScrollPlace>();
  private readonly stopWaitingCallbacks = new Set<() => void>();

  private watching = false;
  private currentNavigationId: number | null = null;
  private currentPath: string | null = null;
  private restoredNavigationId: number | null = null;

  /** Called once by the layout; later calls do nothing. */
  watchNavigation(): void {
    if (this.watching) {
      return;
    }
    this.watching = true;

    // Otherwise the browser restores the old place at once, on the page that is being left.
    if ('scrollRestoration' in history) {
      history.scrollRestoration = 'manual';
    }

    this.router.events.subscribe((event) => {
      if (event instanceof NavigationStart) {
        this.onNavigationStart(event);
      } else if (event instanceof NavigationEnd) {
        this.onNavigationEnd(event);
      }
    });
  }

  /** An area with its own scrolling, such as the screener table, under a key unique on its page. */
  registerArea(key: string, element: HTMLElement): void {
    this.areas.set(key, element);
    this.restoreArea(key, element);
  }

  unregisterArea(key: string, element: HTMLElement): void {
    if (this.areas.get(key) === element) {
      this.areas.delete(key);
    }
  }

  private onNavigationStart(event: NavigationStart): void {
    this.stopWaiting();
    if (this.currentNavigationId !== null) {
      this.savedPlaces.set(this.currentNavigationId, this.currentPlace());
    }
    // Set only for Back and Forward: the id of the history entry being returned to.
    this.restoredNavigationId = event.restoredState?.navigationId ?? null;
  }

  private onNavigationEnd(event: NavigationEnd): void {
    const path = event.urlAfterRedirects.split(/[?#]/)[0];
    const pathChanged = this.currentPath !== null && path !== this.currentPath;
    this.currentPath = path;
    this.currentNavigationId = event.id;

    let savedPlace: PagePlace | undefined;
    if (this.restoredNavigationId !== null) {
      savedPlace = this.savedPlaces.get(this.restoredNavigationId);
    }
    this.restoredNavigationId = null;

    if (savedPlace) {
      this.restore(savedPlace);
    } else if (pathChanged) {
      this.viewportScroller.scrollToPosition([0, 0]);
    }
  }

  private currentPlace(): PagePlace {
    const [left, top] = this.viewportScroller.getScrollPosition();
    const areaPlaces = new Map<string, ScrollPlace>();
    for (const [key, element] of this.areas) {
      areaPlaces.set(key, { left: element.scrollLeft, top: element.scrollTop });
    }
    return { window: { left, top }, areas: areaPlaces };
  }

  private restore(place: PagePlace): void {
    // Areas of the new page may appear only with its data; they are restored when they register.
    this.pendingAreaPlaces = new Map(place.areas);
    for (const [key, element] of this.areas) {
      this.restoreArea(key, element);
    }

    const scrollWindow = (): void => {
      this.viewportScroller.scrollToPosition([place.window.left, place.window.top]);
    };
    this.holdPlace(scrollWindow, [document.body]);
  }

  private restoreArea(key: string, element: HTMLElement): void {
    const place = this.pendingAreaPlaces.get(key);
    if (!place) {
      return;
    }
    this.pendingAreaPlaces.delete(key);

    const scrollArea = (): void => {
      element.scrollTo(place.left, place.top);
    };
    // The area's content (the table) grows when its rows arrive.
    const watched: Element[] = [element];
    if (element.firstElementChild) {
      watched.push(element.firstElementChild);
    }
    this.holdPlace(scrollArea, watched);
  }

  /**
   * Scrolls now and again whenever the watched content changes size, until the reader moves the
   * page, another navigation starts or the time ends. It goes on after the place is first
   * reached: the content may still move, as when a scroll hint appears a frame after its table
   * and the browser keeps the visible rows where they were.
   */
  private holdPlace(scroll: () => void, watched: Element[]): void {
    scroll();
    if (typeof ResizeObserver === 'undefined') {
      return;
    }

    const observer = new ResizeObserver(() => {
      scroll();
    });
    const timer = setTimeout(stop, RESTORE_WAIT_MS);
    const stopWaitingCallbacks = this.stopWaitingCallbacks;

    function stop(): void {
      observer.disconnect();
      clearTimeout(timer);
      for (const eventType of READER_INPUT_EVENTS) {
        window.removeEventListener(eventType, stop);
      }
      stopWaitingCallbacks.delete(stop);
    }

    for (const element of watched) {
      observer.observe(element);
    }
    for (const eventType of READER_INPUT_EVENTS) {
      window.addEventListener(eventType, stop, { passive: true });
    }
    this.stopWaitingCallbacks.add(stop);
  }

  private stopWaiting(): void {
    for (const stop of [...this.stopWaitingCallbacks]) {
      stop();
    }
    this.pendingAreaPlaces.clear();
  }
}
