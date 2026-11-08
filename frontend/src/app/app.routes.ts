import { Routes } from '@angular/router';
import { Dashboard } from './features/dashboard/dashboard';
import { NotFound } from './features/not-found/not-found';
import { Layout } from './layout/layout/layout';

// The screener and the company page load on their first visit: the company page brings the
// chart library, which the other pages do not need.
export const routes: Routes = [
  {
    path: '',
    component: Layout,
    children: [
      {
        path: 'screener',
        loadComponent: () =>
          import('./features/screener/screener').then((module) => module.Screener),
      },
      {
        path: 'stocks/:ticker',
        loadComponent: () =>
          import('./features/stock-details/stock-details').then((module) => module.StockDetails),
      },
      {
        path: '',
        pathMatch: 'full',
        component: Dashboard,
      },
      {
        path: '**',
        component: NotFound,
      },
    ],
  },
];
