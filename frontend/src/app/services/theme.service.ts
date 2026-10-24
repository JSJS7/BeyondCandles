import { DOCUMENT, DestroyRef, inject, Injectable, signal } from '@angular/core';

type Theme = 'light' | 'dark';
const STORAGE_KEY = 'beyondcandles-theme';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly document = inject(DOCUMENT);
  private readonly systemPreference = this.document.defaultView?.matchMedia?.(
    '(prefers-color-scheme: dark)',
  );
  private selectedTheme: Theme | null = null;

  readonly mode = signal<Theme>('light');

  constructor() {
    try {
      const saved = this.document.defaultView?.localStorage.getItem(STORAGE_KEY);
      if (saved === 'light' || saved === 'dark') {
        this.selectedTheme = saved;
      }
    } catch {
      // Browser privacy settings can make persistent storage unavailable.
    }

    this.applyTheme(this.selectedTheme ?? (this.systemPreference?.matches ? 'dark' : 'light'));

    const followSystem = (event: MediaQueryListEvent) => {
      if (this.selectedTheme === null) {
        this.applyTheme(event.matches ? 'dark' : 'light');
      }
    };
    this.systemPreference?.addEventListener('change', followSystem);
    inject(DestroyRef).onDestroy(() => {
      this.systemPreference?.removeEventListener('change', followSystem);
    });
  }

  toggle(): void {
    this.selectedTheme = this.mode() === 'dark' ? 'light' : 'dark';
    this.applyTheme(this.selectedTheme);
    try {
      this.document.defaultView?.localStorage.setItem(STORAGE_KEY, this.selectedTheme);
    } catch {
      // Switching still works for this session when storage is blocked.
    }
  }

  private applyTheme(theme: Theme): void {
    this.document.documentElement.dataset['theme'] = theme;
    this.mode.set(theme);
  }
}
