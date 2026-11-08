import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { Sidebar } from './sidebar';

@Component({ template: '<app-sidebar />', imports: [Sidebar] })
class PageWithSidebar {}

describe('Sidebar', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      providers: [
        provideRouter([
          { path: '', pathMatch: 'full', component: PageWithSidebar },
          { path: 'screener', component: PageWithSidebar },
          { path: 'stocks/:ticker', component: PageWithSidebar },
        ]),
      ],
    }).compileComponents();
  });

  async function activeLinks(url: string): Promise<(string | null)[]> {
    const harness = await RouterTestingHarness.create(url);
    harness.detectChanges();
    const links = harness.routeNativeElement?.querySelectorAll('a[aria-current="page"]') ?? [];
    return [...links].map((link) => link.getAttribute('href'));
  }

  it('marks only the screener link as current on the screener page', async () => {
    expect(await activeLinks('/screener?sector=Energy')).toEqual(['/screener']);
  });

  it('marks only the dashboard link as current on the dashboard', async () => {
    expect(await activeLinks('/')).toEqual(['/']);
  });

  it('marks no link on a company page, which has no entry in the menu', async () => {
    // Regression test (N69): the dashboard link used to stay highlighted on every page.
    expect(await activeLinks('/stocks/AAPL')).toEqual([]);
  });
});
