import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { RatioKey } from '../../models/screener';
import { Backtest } from './backtest';
import { backtestResponse, ratioBacktest } from './backtest-test-data';

const API = 'http://127.0.0.1:8000/backtest';
const SUMMARY = backtestResponse([ratioBacktest('pe'), ratioBacktest('roa')]);

function detail(ratio: RatioKey) {
  return backtestResponse([ratioBacktest(ratio, true)]);
}

describe('Backtest page', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      providers: [
        provideRouter([
          { path: 'backtest', component: Backtest },
          { path: 'screener', component: Backtest },
        ]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  async function open(url: string, ratio: RatioKey): Promise<HTMLElement> {
    harness = await RouterTestingHarness.create(url);
    http.expectOne(API).flush(SUMMARY);
    http.expectOne(`${API}/${ratio}`).flush(detail(ratio));
    harness.detectChanges();
    return harness.routeNativeElement as HTMLElement;
  }

  it('opens on P/E and lists every ratio of the answer in the screener order', async () => {
    const page = await open('/backtest', 'pe');

    const rows = [...page.querySelectorAll('.summary-table tbody .ratio-link')];
    expect(rows.map((row) => row.textContent?.trim())).toEqual(['P/E', 'ROA']);
    expect(page.querySelector('#ratio-title')?.textContent).toContain('P/E');
    expect(page.querySelector('.selected-row .ratio-link')?.textContent).toContain('P/E');
  });

  it('reads the ratio from the address and says when financial firms are left out', async () => {
    const page = await open('/backtest?ratio=roa', 'roa');

    expect(page.querySelector('#ratio-title')?.textContent).toContain('ROA');
    expect(page.querySelector('.ratio-facts')?.textContent).toContain(
      'Financial firms are left out',
    );
  });

  it('switches the tables between returns after and before costs', async () => {
    const page = await open('/backtest', 'pe');
    const firstQuintile = () =>
      page.querySelector('.years-table tbody tr td.number-column')?.textContent?.trim();
    // After costs by default: 0.2 - 0.01.
    expect(firstQuintile()).toBe('19.0%');

    const before = [...page.querySelectorAll<HTMLButtonElement>('.costs-button')].find((button) =>
      button.textContent?.includes('Before costs'),
    );
    before?.click();
    harness.detectChanges();

    expect(firstQuintile()).toBe('20.0%');
    expect(before?.getAttribute('aria-pressed')).toBe('true');
  });

  it('keeps the partial year apart from the full years and their summary', async () => {
    const page = await open('/backtest', 'pe');

    const labels = [...page.querySelectorAll('.years-table')][0].querySelectorAll('tbody th');
    const texts = [...labels].map((label) => label.querySelector('.year-label')?.textContent);
    expect(texts).toEqual(['2017', '2018', 'Per year', 'Apr–Dec 2025']);
  });

  it('lists the companies of each quintile and links the same ranking in the screener', async () => {
    const page = await open('/backtest', 'pe');

    const firstQuintile = page.querySelector('.quintile');
    const tickers = [...(firstQuintile?.querySelectorAll('tbody th a') ?? [])];
    expect(tickers.map((link) => link.textContent)).toEqual(['A1', 'B1']);
    expect(firstQuintile?.querySelector('tbody td')?.textContent).toBe('12.50');
    const screenerLink = page.querySelector('.coverage-note a');
    expect(screenerLink?.getAttribute('href')).toBe('/screener?date=2017-03-31&sort=pe&dir=asc');
  });

  it('loads the companies of a newly chosen ratio and keeps the summary', async () => {
    await open('/backtest', 'pe');

    await TestBed.inject(Router).navigateByUrl('/backtest?ratio=roa');
    harness.detectChanges();

    http.expectNone(API);
    http.expectOne(`${API}/roa`).flush(detail('roa'));
  });

  it('offers Try again after a failed summary and loads it on request', async () => {
    harness = await RouterTestingHarness.create('/backtest');
    http.expectOne(API).flush('down', { status: 503, statusText: 'Unavailable' });
    http.expectOne(`${API}/pe`).flush(detail('pe'));
    harness.detectChanges();
    const page = harness.routeNativeElement as HTMLElement;

    const retry = page.querySelector<HTMLButtonElement>('.retry-button');
    expect(retry).not.toBeNull();
    retry?.click();
    http.expectOne(API).flush(SUMMARY);
    harness.detectChanges();

    expect(page.querySelector('.summary-table')).not.toBeNull();
  });
});
