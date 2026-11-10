import { ActivatedRouteSnapshot, Routes } from '@angular/router';
import { Dashboard } from './features/dashboard/dashboard';
import { NotFound } from './features/not-found/not-found';
import { Layout } from './layout/layout/layout';
import { pageTitle } from './shared/page-title';

/** The ticker until the company page knows the company's name and puts it in the title. */
function stockPageTitle(route: ActivatedRouteSnapshot): string {
  const ticker = route.paramMap.get('ticker') ?? 'Company';
  return pageTitle(ticker);
}

// The screener and the company page load on their first visit: the company page brings the
// chart library, which the other pages do not need.
export const routes: Routes = [
  {
    path: '',
    component: Layout,
    children: [
      {
        path: 'screener',
        title: pageTitle('Screener'),
        loadComponent: () =>
          import('./features/screener/screener').then((module) => module.Screener),
      },
      {
        path: 'stocks/:ticker',
        title: stockPageTitle,
        loadComponent: () =>
          import('./features/stock-details/stock-details').then((module) => module.StockDetails),
      },
      {
        path: '',
        pathMatch: 'full',
        title: pageTitle('Dashboard'),
        component: Dashboard,
      },
      {
        path: '**',
        title: pageTitle('Page not found'),
        component: NotFound,
      },
    ],
  },
];
