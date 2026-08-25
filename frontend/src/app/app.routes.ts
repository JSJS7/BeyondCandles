import { Routes } from '@angular/router';
import { Dashboard } from './features/dashboard/dashboard';
import { LayoutComponent } from './layout/layout/layout';

export const routes: Routes = [
	{
		path: '',
		component: LayoutComponent,
		children: [
			{
				path: '',
				component: Dashboard,
			},
		],
	},
];
