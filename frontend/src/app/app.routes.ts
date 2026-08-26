import { Routes } from '@angular/router';
import { Dashboard } from './features/dashboard/dashboard';
import { NotFoundComponent } from './features/not-found/not-found';
import { StockDetailsComponent } from './features/stock-details/stock-details';
import { LayoutComponent } from './layout/layout/layout';

export const routes: Routes = [
	{
		path: '',
		component: LayoutComponent,
		children: [
			{
				path: 'stocks/:ticker',
				component: StockDetailsComponent,
			},
			{
				path: '',
				pathMatch: 'full',
				component: Dashboard,
			},
			{
				path: '**',
				component: NotFoundComponent,
			},
		],
	},
];
