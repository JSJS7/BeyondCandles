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
          { path: 'backtest', component: PageWithSidebar },
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

  it('marks no link on a company page, which has no entry in the menu', async () => {
    // Regression test (N69): a menu link used to stay highlighted on every page.
    expect(await activeLinks('/stocks/AAPL')).toEqual([]);
  });

  it('marks only the backtest link as current on the backtest page', async () => {
    expect(await activeLinks('/backtest?ratio=pb')).toEqual(['/backtest']);
  });

  it('links to the screener and the backtest, both real links', async () => {
    // N24: an entry that looks clickable but does nothing misleads, so every entry is a link.
    const harness = await RouterTestingHarness.create('/screener');
    harness.detectChanges();
    const menu = harness.routeNativeElement as HTMLElement;

    const links = [...menu.querySelectorAll('.navigation a')];
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['/screener', '/backtest']);
  });
});
