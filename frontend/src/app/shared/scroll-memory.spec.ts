import { Location, ViewportScroller } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { EnvironmentProviders, Provider } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { ScrollMemory } from './scroll-memory';

describe('ScrollMemory', () => {
  let router: Router;
  let location: Location;
  let scrollMemory: ScrollMemory;
  // A fake window: it scrolls no further than its content allows, like a real one.
  let windowTop: number;
  let windowBottomLimit: number;
  // Callbacks of the observers still connected; a real observer stops after disconnect.
  let resizeCallbacks: Set<() => void>;

  const routes = [
    { path: '', children: [] },
    { path: 'screener', children: [] },
    { path: 'stocks/:ticker', children: [] },
    { path: 'backtest', children: [] },
  ];

  /**
   * Starts the app as the layout does. With a location from an earlier start it acts as a reload
   * of the page: the history entries and their states stay, the router counts from 1 again.
   */
  async function startApp(earlierLocation?: Location): Promise<void> {
    const providers: (Provider | EnvironmentProviders)[] = [
      provideRouter(routes),
      provideLocationMocks(),
    ];
    if (earlierLocation) {
      providers.push({ provide: Location, useValue: earlierLocation });
    }
    TestBed.configureTestingModule({ providers });
    router = TestBed.inject(Router);
    location = TestBed.inject(Location);
    scrollMemory = TestBed.inject(ScrollMemory);

    const viewportScroller = TestBed.inject(ViewportScroller);
    vi.spyOn(viewportScroller, 'getScrollPosition').mockImplementation(() => [0, windowTop]);
    vi.spyOn(viewportScroller, 'scrollToPosition').mockImplementation(([, top]) => {
      windowTop = Math.min(top, windowBottomLimit);
    });

    scrollMemory.watchNavigation();
    // As in the app: the router listens to Back and Forward only after its first navigation.
    router.initialNavigation();
    await new Promise((resolve) => setTimeout(resolve));
  }

  beforeEach(async () => {
    sessionStorage.clear();
    windowTop = 0;
    windowBottomLimit = 10_000;

    // jsdom has no ResizeObserver; this one lets a test say when the content has grown.
    resizeCallbacks = new Set();
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(private readonly callback: () => void) {}
        observe(): void {
          resizeCallbacks.add(this.callback);
        }
        disconnect(): void {
          resizeCallbacks.delete(this.callback);
        }
      },
    );

    await startApp();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  function contentGrows(): void {
    for (const callback of [...resizeCallbacks]) {
      callback();
    }
  }

  async function goBack(): Promise<void> {
    // The router handles the history event asynchronously.
    location.back();
    await new Promise((resolve) => setTimeout(resolve));
  }

  it('starts a new page at the top, but keeps the place when only the query changes', async () => {
    // W34: the screener writes its state to the query on every keystroke, which must not scroll.
    await router.navigateByUrl('/screener');
    windowTop = 900;

    await router.navigateByUrl('/screener?q=micro', { replaceUrl: true });
    expect(windowTop).toBe(900);

    await router.navigateByUrl('/stocks/AAPL');
    expect(windowTop).toBe(0);
  });

  it('returns to the place where the page was left after Back', async () => {
    await router.navigateByUrl('/screener');
    windowTop = 1200;
    await router.navigateByUrl('/stocks/AAPL');

    await goBack();

    expect(location.path()).toBe('/screener');
    expect(windowTop).toBe(1200);
  });

  it('starts at the top when the left page is opened again by a link, not by Back', async () => {
    // Only a history entry has a saved place; the menu link opens the screener anew.
    await router.navigateByUrl('/screener');
    windowTop = 1200;
    await router.navigateByUrl('/stocks/AAPL');

    await router.navigateByUrl('/screener');

    expect(windowTop).toBe(0);
  });

  it('waits until the page has grown tall enough, as when its data loads again', async () => {
    await router.navigateByUrl('/screener');
    windowTop = 1200;
    await router.navigateByUrl('/stocks/AAPL');
    // After Back the screener shows only its loading message at first.
    windowBottomLimit = 300;

    await goBack();
    expect(windowTop).toBe(300);

    windowBottomLimit = 5000;
    contentGrows();
    expect(windowTop).toBe(1200);
  });

  it('stops waiting when the reader moves the page by themselves', async () => {
    // The reader has chosen a place; data arriving later must not pull the page away from it.
    await router.navigateByUrl('/screener');
    windowTop = 1200;
    await router.navigateByUrl('/stocks/AAPL');
    windowBottomLimit = 300;
    await goBack();

    window.dispatchEvent(new Event('wheel'));
    windowTop = 100;
    windowBottomLimit = 5000;
    contentGrows();

    expect(windowTop).toBe(100);
  });

  it('holds the place when the content moves after it was reached', async () => {
    // The backtest's scroll hints appear a frame after their tables; the browser then keeps the
    // visible rows in view and moves the page 30 px down unless the place is applied again.
    await router.navigateByUrl('/backtest');
    windowTop = 1500;
    await router.navigateByUrl('/stocks/AAPL');
    await goBack();
    expect(windowTop).toBe(1500);

    windowTop = 1530;
    contentGrows();

    expect(windowTop).toBe(1500);
  });

  it('returns an area with its own scrolling to its place, also when it appears later', async () => {
    // The screener table scrolls by itself and is drawn again after Back.
    const firstTable = document.createElement('div');
    await router.navigateByUrl('/screener');
    scrollMemory.registerArea('results', firstTable);
    firstTable.scrollTop = 2400;
    firstTable.scrollLeft = 80;
    await router.navigateByUrl('/stocks/AAPL');
    scrollMemory.unregisterArea('results', firstTable);

    await goBack();
    const secondTable = document.createElement('div');
    const scrollTo = vi.fn();
    secondTable.scrollTo = scrollTo as typeof secondTable.scrollTo;
    scrollMemory.registerArea('results', secondTable);

    expect(scrollTo).toHaveBeenCalledWith(80, 2400);
  });

  /** Leaves the screener at the top for a company page scrolled down, and reloads that page. */
  async function reloadCompanyPage(): Promise<void> {
    await router.navigateByUrl('/screener');
    await router.navigateByUrl('/stocks/AAPL');
    windowTop = 700;

    window.dispatchEvent(new Event('pagehide'));
    const earlierLocation = location;
    TestBed.resetTestingModule();
    // A reloaded page starts at the top.
    windowTop = 0;
    await startApp(earlierLocation);
  }

  it('after a reload does not give an older page the place of another one', async () => {
    // Regression test: the router numbers its navigations from 1 again after a reload. Here the
    // start page's old entry carries number 1, which after the reload is the company page's
    // number, so the second Back took the company page's place for the start page.
    await reloadCompanyPage();
    windowTop = 700;

    await goBack();
    expect(location.path()).toBe('/screener');
    expect(windowTop).toBe(0);

    await goBack();
    expect(location.path()).toBe('/');
    expect(windowTop).toBe(0);
  });

  it('returns a reloaded page to the place where it was', async () => {
    // The browser does this by itself, but the memory turned its restoring off.
    await reloadCompanyPage();

    expect(location.path()).toBe('/stocks/AAPL');
    expect(windowTop).toBe(700);
  });

  it('keeps one place for an entry whose query is replaced on every keystroke', async () => {
    // Each replaced query must not count as a new entry, or the places of older pages would be
    // dropped to make room for them.
    await router.navigateByUrl('/backtest');
    windowTop = 1500;
    await router.navigateByUrl('/screener');
    for (let keystroke = 1; keystroke <= 60; keystroke++) {
      await router.navigateByUrl(`/screener?q=${'x'.repeat(keystroke)}`, { replaceUrl: true });
    }
    await router.navigateByUrl('/stocks/AAPL');

    await goBack();
    await goBack();

    expect(location.path()).toBe('/backtest');
    expect(windowTop).toBe(1500);
  });
});
