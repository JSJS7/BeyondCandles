import { TestBed } from '@angular/core/testing';
import { ThemeService } from './theme.service';

describe('ThemeService', () => {
  const storageKey = 'beyondcandles-theme';
  let listeners: Set<(event: MediaQueryListEvent) => void>;

  beforeEach(() => {
    localStorage.removeItem(storageKey);
    listeners = new Set();
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        matches: true,
        addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) =>
          listeners.add(listener),
        removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) =>
          listeners.delete(listener),
      })),
    );
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.removeItem(storageKey);
    document.documentElement.removeAttribute('data-theme');
  });

  function systemChangesToDark(dark: boolean): void {
    for (const listener of listeners) {
      listener({ matches: dark } as MediaQueryListEvent);
    }
  }

  it('follows system changes until the user chooses a theme and removes its listener on destruction', () => {
    const theme = TestBed.inject(ThemeService);
    expect(theme.mode()).toBe('dark');
    systemChangesToDark(false);
    expect(theme.mode()).toBe('light');
    expect(document.documentElement.dataset['theme']).toBe('light');

    theme.toggle();
    systemChangesToDark(false);
    expect(theme.mode()).toBe('dark');
    TestBed.resetTestingModule();
    expect(listeners.size).toBe(0);
  });

  it('restores a saved preference and persists the next selection', () => {
    localStorage.setItem(storageKey, 'light');
    const theme = TestBed.inject(ThemeService);
    expect(theme.mode()).toBe('light');
    systemChangesToDark(true);
    expect(theme.mode()).toBe('light');

    theme.toggle();
    expect(localStorage.getItem(storageKey)).toBe('dark');
    expect(document.documentElement.dataset['theme']).toBe('dark');
    TestBed.resetTestingModule();
    expect(TestBed.inject(ThemeService).mode()).toBe('dark');
  });

  it('ignores an invalid saved value and uses the system preference', () => {
    localStorage.setItem(storageKey, 'invalid-theme');
    expect(TestBed.inject(ThemeService).mode()).toBe('dark');
  });

  it('keeps switching available when browser storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Storage blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage blocked');
    });
    const theme = TestBed.inject(ThemeService);
    expect(theme.mode()).toBe('dark');
    expect(() => theme.toggle()).not.toThrow();
    expect(theme.mode()).toBe('light');
    expect(document.documentElement.dataset['theme']).toBe('light');
  });
});
