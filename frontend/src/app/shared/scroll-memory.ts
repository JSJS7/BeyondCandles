import { Location, ViewportScroller } from '@angular/common';
import { inject, Injectable, OnDestroy } from '@angular/core';
import { NavigationEnd, NavigationStart, Router } from '@angular/router';
import { Subscription } from 'rxjs';

// How long a restored place is held while the page grows and settles, for example while the
// screener loads its data again after Back.
const RESTORE_WAIT_MS = 10_000;
// A reader who moves the page during that time has chosen a place of their own.
const READER_INPUT_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'];
// The places are kept in the tab's session storage, so that they survive a reload of the page.
const STORAGE_KEY = 'bc-scroll-places';
// Enough for a long visit; the places of the oldest history entries are dropped first.
const MAX_SAVED_PLACES = 50;
// The field of a history entry's state that names the entry's saved place.
const ENTRY_KEY_FIELD = 'scrollPlaceKey';

interface ScrollPlace {
  left: number;
  top: number;
}

interface PagePlace {
  window: ScrollPlace;
  /** Places of the areas with their own scrolling (scroll-memory-area.ts), by their keys. */
  areas: Record<string, ScrollPlace>;
}

type HistoryState = Record<string, unknown>;

/**
 * Where each page was left, so that Back, Forward and a reload return to the same place.
 *
 * A new page starts at the top (W34). A change of the query string alone keeps the place: the
 * screener writes its state there on every keystroke. The router's own scrolling
 * (withInMemoryScrolling) cannot tell the two apart, restores only the window and does not wait
 * for data, so the places are kept here.
 *
 * Each history entry carries a key in its state, and the place is saved under that key. The key
 * joins the router's navigation number with a name of this load of the app: the router numbers
 * its navigations from 1 again after a reload, so the number alone would let an entry from before
 * the reload take the place saved for another page.
 */
@Injectable({ providedIn: 'root' })
export class ScrollMemory implements OnDestroy {
  private readonly router = inject(Router);
  private readonly location = inject(Location);
  private readonly viewportScroller = inject(ViewportScroller);

  private readonly loadId = newLoadId();
  private readonly savedPlaces = readSavedPlaces();
  private readonly areas = new Map<string, HTMLElement>();
  private pendingAreaPlaces = new Map<string, ScrollPlace>();
  private readonly stopWaitingCallbacks = new Set<() => void>();

  private routerEvents: Subscription | null = null;
  private currentEntryKey: string | null = null;
  private currentPath: string | null = null;
  // Set between the start and the end of a navigation.
  private restoredEntryKey: string | null = null;
  private sameEntry = false;

  // A reload has no router navigation to save the place at, so it is saved when the page goes.
  private readonly onPageHide = (): void => {
    this.saveCurrentPlace();
  };

  /** Called once at the start of the app (app.config.ts); later calls do nothing. */
  watchNavigation(): void {
    if (this.routerEvents) {
      return;
    }

    // Otherwise the browser restores the old place at once, on the page that is being left.
    if ('scrollRestoration' in history) {
      history.scrollRestoration = 'manual';
    }

    this.routerEvents = this.router.events.subscribe((event) => {
      if (event instanceof NavigationStart) {
        this.onNavigationStart(event);
      } else if (event instanceof NavigationEnd) {
        this.onNavigationEnd(event);
      }
    });
    window.addEventListener('pagehide', this.onPageHide);
  }

  ngOnDestroy(): void {
    this.stopWaiting();
    this.routerEvents?.unsubscribe();
    window.removeEventListener('pagehide', this.onPageHide);
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
    this.saveCurrentPlace();

    // Back and Forward give the state of the entry being returned to. The first navigation after
    // a reload also finds the state of its entry, which still holds the key from before.
    let state: HistoryState | null = event.restoredState ?? null;
    const firstNavigation = this.currentEntryKey === null;
    if (!state && firstNavigation) {
      state = this.location.getState() as HistoryState | null;
    }
    const restoredKey = state?.[ENTRY_KEY_FIELD];
    this.restoredEntryKey = typeof restoredKey === 'string' ? restoredKey : null;

    // A replaced address, as the screener's query on every keystroke, stays the same history
    // entry. Back and Forward also replace the address, but they move to another entry.
    const replacesAddress = this.router.currentNavigation()?.extras.replaceUrl === true;
    const movesInHistory = event.navigationTrigger === 'popstate';
    this.sameEntry = replacesAddress && !movesInHistory && !firstNavigation;
  }

  private onNavigationEnd(event: NavigationEnd): void {
    const path = event.urlAfterRedirects.split(/[?#]/)[0];
    const pathChanged = this.currentPath !== null && path !== this.currentPath;
    this.currentPath = path;

    let savedPlace: PagePlace | undefined;
    if (this.restoredEntryKey !== null) {
      this.currentEntryKey = this.restoredEntryKey;
      savedPlace = this.savedPlaces.get(this.restoredEntryKey);
    } else if (!this.sameEntry || this.currentEntryKey === null) {
      this.currentEntryKey = `${this.loadId}-${event.id}`;
    }
    this.restoredEntryKey = null;
    this.sameEntry = false;
    this.markCurrentEntry(this.currentEntryKey);

    if (savedPlace) {
      this.restore(savedPlace);
    } else if (pathChanged) {
      this.viewportScroller.scrollToPosition([0, 0]);
    }
  }

  /** Writes the key into the history entry's state, keeping what the router stored there. */
  private markCurrentEntry(entryKey: string): void {
    const state = this.location.getState() as HistoryState | null;
    if (state?.[ENTRY_KEY_FIELD] === entryKey) {
      return;
    }
    this.location.replaceState(this.location.path(true), '', {
      ...state,
      [ENTRY_KEY_FIELD]: entryKey,
    });
  }

  private saveCurrentPlace(): void {
    if (this.currentEntryKey === null) {
      return;
    }
    // Deleted first, so that the entry moves to the end of the order and is dropped last.
    this.savedPlaces.delete(this.currentEntryKey);
    this.savedPlaces.set(this.currentEntryKey, this.currentPlace());
    for (const oldestKey of this.savedPlaces.keys()) {
      if (this.savedPlaces.size <= MAX_SAVED_PLACES) {
        break;
      }
      this.savedPlaces.delete(oldestKey);
    }
    writeSavedPlaces(this.savedPlaces);
  }

  private currentPlace(): PagePlace {
    const [left, top] = this.viewportScroller.getScrollPosition();
    const areaPlaces: Record<string, ScrollPlace> = {};
    for (const [key, element] of this.areas) {
      areaPlaces[key] = { left: element.scrollLeft, top: element.scrollTop };
    }
    return { window: { left, top }, areas: areaPlaces };
  }

  private restore(place: PagePlace): void {
    // Areas of the new page may appear only with its data; they are restored when they register.
    this.pendingAreaPlaces = new Map(Object.entries(place.areas));
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

/** A name for this load of the app; it only has to differ from the names of earlier loads. */
function newLoadId(): string {
  const time = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 8);
  return `${time}${random}`;
}

function isScrollPlace(value: unknown): value is ScrollPlace {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const place = value as Record<string, unknown>;
  return typeof place['left'] === 'number' && typeof place['top'] === 'number';
}

function isPagePlace(value: unknown): value is PagePlace {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const place = value as Record<string, unknown>;
  const areas = place['areas'];
  if (!isScrollPlace(place['window']) || typeof areas !== 'object' || areas === null) {
    return false;
  }
  return Object.values(areas).every(isScrollPlace);
}

/**
 * The places saved earlier in this tab, oldest first. Storage may be blocked or hold something
 * else under the same name; the memory then starts empty and works for this load only.
 */
function readSavedPlaces(): Map<string, PagePlace> {
  const places = new Map<string, PagePlace>();
  try {
    const text = sessionStorage.getItem(STORAGE_KEY);
    if (text === null) {
      return places;
    }
    const entries: unknown = JSON.parse(text);
    if (!Array.isArray(entries)) {
      return places;
    }
    for (const entry of entries) {
      const isPair = Array.isArray(entry) && entry.length === 2;
      if (isPair && typeof entry[0] === 'string' && isPagePlace(entry[1])) {
        places.set(entry[0], entry[1]);
      }
    }
  } catch {
    // Unreadable storage: start empty.
  }
  return places;
}

function writeSavedPlaces(places: Map<string, PagePlace>): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...places]));
  } catch {
    // Blocked or full storage: the places stay in memory for this load.
  }
}
