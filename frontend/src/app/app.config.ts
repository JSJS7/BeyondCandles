import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideRouter } from '@angular/router';
import { routes } from './app.routes';
import { provideHttpClient } from '@angular/common/http';
import { ScrollMemory } from './shared/scroll-memory';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    provideHttpClient(),
    // Before the first navigation, which a reload starts: the layout is created only during it.
    provideAppInitializer(() => {
      inject(ScrollMemory).watchNavigation();
    }),
  ],
};
