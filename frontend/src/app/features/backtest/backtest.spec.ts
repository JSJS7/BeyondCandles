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

  it('shows the ratio from the address also in the list of ratios', async () => {
    // Regression test (review 18.11.2026, B1): the list showed P/E while the tables showed ROA,
    // because its value was set before the options existed.
    const page = await open('/backtest?ratio=roa', 'roa');

    const select = page.querySelector<HTMLSelectElement>('#backtest-ratio');
    expect(select?.value).toBe('roa');
  });

  it('shows the test of the ranking: the mean 1 - 5 and its t', async () => {
    const page = await open('/backtest', 'pe');

    const cells = [...page.querySelectorAll('.summary-table tbody tr')][0].querySelectorAll('td');
    const texts = [...cells].map((cell) => cell.textContent?.trim());
    expect(texts).toContain('+20.0');
    expect(texts).toContain('1.50');
    expect(page.querySelector('.spread-test')?.textContent).toContain('t 1.50, from 24 monthly');
  });

  it('keeps the chosen year when another ratio is chosen', async () => {
    const page = await open('/backtest', 'pe');
    const yearSelect = page.querySelector<HTMLSelectElement>('#holding-year');
    if (yearSelect) {
      yearSelect.value = '2018-03-31';
      yearSelect.dispatchEvent(new Event('change'));
    }
    harness.detectChanges();

    await TestBed.inject(Router).navigateByUrl('/backtest?ratio=roa');
    http.expectOne(`${API}/roa`).flush(detail('roa'));
    harness.detectChanges();

    expect(page.querySelector<HTMLSelectElement>('#holding-year')?.value).toBe('2018-03-31');
    expect(page.querySelector('.coverage-note')?.textContent).toContain('31 Mar 2018');
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

    // Row n holds the n-th company of every quintile, three columns each.
    const rows = [...page.querySelectorAll('.members-table tbody tr')];
    const tickersOf = (row: Element) =>
      [...row.querySelectorAll('a')].map((link) => link.textContent);
    expect(rows.map(tickersOf)).toEqual([
      ['A1', 'A2', 'A3', 'A4', 'A5'],
      ['B1', 'B2', 'B3', 'B4', 'B5'],
    ]);
    expect(rows[0].querySelectorAll('td')[1].textContent?.trim()).toBe('12.50');
    const screenerLink = page.querySelector('.coverage-note a');
    expect(screenerLink?.getAttribute('href')).toBe('/screener?date=2017-03-31&sort=pe&dir=asc');
  });

  it('leaves the cells of a shorter quintile empty, so the columns stay in line', async () => {
    // With 171 companies the first quintile holds 35 and the last 34: the table's last row
    // must keep the last quintile's three columns, empty.
    harness = await RouterTestingHarness.create('/backtest');
    http.expectOne(API).flush(SUMMARY);
    const answer = detail('pe');
    answer.ratios[0].periods[0].quintiles[4].members?.pop();
    http.expectOne(`${API}/pe`).flush(answer);
    harness.detectChanges();
    const page = harness.routeNativeElement as HTMLElement;

    const lastRow = [...page.querySelectorAll('.members-table tbody tr')][1];
    const cells = [...lastRow.querySelectorAll('td')];
    expect(cells.length).toBe(15);
    expect(cells.slice(12).map((cell) => cell.textContent?.trim())).toEqual(['', '', '']);
    expect(cells[9].textContent?.trim()).toBe('B4');
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

  it('brings the chosen ratio into view after a plain click in the summary only', async () => {
    // Review 19.11.2026: the ratio's tables start below the first screen, so a click in the
    // summary changed only the highlighted row in view. Ctrl + click opens a tab instead.
    const page = await open('/backtest', 'pe');
    const scrolled = vi.fn();
    const originalScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrolled;
    onTestFinished(() => {
      Element.prototype.scrollIntoView = originalScroll;
    });
    const roaLink = [...page.querySelectorAll<HTMLAnchorElement>('.ratio-link')].find(
      (link) => link.textContent?.trim() === 'ROA',
    );

    roaLink?.dispatchEvent(
      new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true }),
    );
    expect(scrolled).not.toHaveBeenCalled();

    roaLink?.click();
    await harness.fixture.whenStable();
    http.expectOne(`${API}/roa`).flush(detail('roa'));
    harness.detectChanges();

    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(document.activeElement?.id).toBe('backtest-ratio');
    expect(page.querySelector<HTMLSelectElement>('#backtest-ratio')?.value).toBe('roa');
  });

  it('loads the summary and the companies with one Try again when both failed', async () => {
    // Regression test (review 19.11.2026): with the API down both requests fail, but only the
    // summary's error shows; one Try again left the companies' error behind, so a second was
    // needed.
    harness = await RouterTestingHarness.create('/backtest');
    http.expectOne(API).flush('down', { status: 503, statusText: 'Unavailable' });
    http.expectOne(`${API}/pe`).flush('down', { status: 503, statusText: 'Unavailable' });
    harness.detectChanges();
    const page = harness.routeNativeElement as HTMLElement;

    page.querySelector<HTMLButtonElement>('.retry-button')?.click();
    http.expectOne(API).flush(SUMMARY);
    http.expectOne(`${API}/pe`).flush(detail('pe'));
    harness.detectChanges();

    expect(page.querySelector('.retry-button')).toBeNull();
    expect(page.querySelectorAll('.members-table tbody a').length).toBeGreaterThan(0);
  });
});
