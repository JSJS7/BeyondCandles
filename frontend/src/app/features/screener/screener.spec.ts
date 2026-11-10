import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { Ratios, ScreenerResponse, ScreenerRow } from '../../models/screener';
import { Screener } from './screener';
import { SCREENER_CELL_LABELS } from './screener-columns';

const API = 'http://127.0.0.1:8000/screener';

const NO_RATIOS: Ratios = {
  pe: null,
  pb: null,
  ps: null,
  roe: null,
  roa: null,
  net_margin: null,
  operating_margin: null,
  revenue_growth: null,
  eps_growth: null,
  ocf_to_net_income: null,
  fcf_margin: null,
  equity_to_assets: null,
};

function company(ticker: string, sector: string, ratios: Partial<Ratios>): ScreenerRow {
  return {
    ticker,
    name: `${ticker} Corporation`,
    sector,
    latest: {
      period_start: '2024-01-01',
      period_end: '2024-12-31',
      price_date: '2025-12-31',
      price: '50.5',
      eps_basis: 'diluted',
      ocf_scope: 'total',
      shares_basis: 'reported',
      revenue_basis_break: false,
      eps_basis_break: false,
      spin_offs: [],
      ratios: { ...NO_RATIOS, ...ratios },
    },
  };
}

const THREE_COMPANIES: ScreenerResponse = {
  valuation_date: '2025-12-31',
  rows: [
    company('AAA', 'Energy', { pe: '10', roe: '0.2' }),
    company('BBB', 'Energy', { pe: null, roe: '0' }),
    company('CCC', 'Technology', { pe: '30', roe: '0.1' }),
  ],
};

describe('Screener page', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: 'screener', component: Screener }]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  async function open(url = '/screener', response = THREE_COMPANIES): Promise<void> {
    harness = await RouterTestingHarness.create(url);
    const date = new URL(url, 'http://test').searchParams.get('date') ?? '2025-12-31';
    http.expectOne(`${API}?date=${date}`).flush(response);
    harness.detectChanges();
  }

  function page(): HTMLElement {
    return harness.routeNativeElement as HTMLElement;
  }

  function shownTickers(): string[] {
    const tickers = page().querySelectorAll('tbody .ticker');
    return [...tickers].map((ticker) => ticker.textContent?.trim() ?? '');
  }

  function headerButton(label: string): HTMLButtonElement {
    const buttons = [...page().querySelectorAll<HTMLButtonElement>('.sort-button')];
    const button = buttons.find((candidate) => candidate.textContent?.trim().startsWith(label));
    if (!button) {
      throw new Error(`No sort button ${label}`);
    }
    return button;
  }

  async function settle(): Promise<void> {
    await harness.fixture.whenStable();
    harness.detectChanges();
  }

  async function changeControl(selector: string, value: string): Promise<void> {
    const control = page().querySelector<HTMLInputElement | HTMLSelectElement>(selector);
    if (!control) {
      throw new Error(`No control ${selector}`);
    }
    control.value = value;
    control.dispatchEvent(new Event('change'));
    await settle();
  }

  it('loads the default date and lists companies with a link to each company page', async () => {
    await open();

    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(page().querySelector('tbody a')?.getAttribute('href')).toBe('/stocks/AAA');
    expect(page().textContent).toContain('3 of 3 companies, valued at 31 Dec 2025');
    expect(page().querySelector<HTMLInputElement>('#valuation-date')?.value).toBe('2025-12-31');
  });

  it('explains every label that a cell can carry in the reading guide', async () => {
    // K15: the type of cellLabels allows only these labels, and a label that the guide does
    // not explain tells the reader nothing.
    await open();

    const guideLabels = page().querySelectorAll('.reading-guide dt .metric-label');
    const explained = [...guideLabels].map((label) => label.textContent?.trim());
    for (const label of SCREENER_CELL_LABELS) {
      expect(explained).toContain(label);
    }
  });

  it('names the valuation date as a readable date while the ratios load', async () => {
    // N103: the rest of the page writes "31 Mar 2021", never the ISO text of the address.
    harness = await RouterTestingHarness.create('/screener?date=2021-03-31');
    harness.detectChanges();

    expect(page().textContent).toContain('Loading ratios for 31 Mar 2021...');
    http.expectOne(`${API}?date=2021-03-31`).flush(THREE_COMPANIES);
  });

  it('links the comparability note to the methodology', async () => {
    await open();

    const link = page().querySelector('.comparability-note a');
    expect(link?.getAttribute('href')).toContain('METHODOLOGY.md');
  });

  it('shows a dash for an empty ratio and 0.0% for a true zero', async () => {
    await open();

    const bbbRow = page().querySelectorAll('tbody tr')[1];
    const cellTexts = [...bbbRow.querySelectorAll('td')].map((cell) => cell.textContent ?? '');
    const peCell = cellTexts[3];
    const roeCell = cellTexts[6];

    expect(peCell).toContain('—');
    expect(peCell).toContain('No value');
    expect(peCell).not.toContain('0.00');
    expect(roeCell).toContain('0.0%');
  });

  it('sorts by a column, marks it for assistive technology and names the missing count', async () => {
    await open();

    headerButton('P/E').click();
    await settle();

    // Largest first, and BBB without a value stays last.
    expect(shownTickers()).toEqual(['CCC', 'AAA', 'BBB']);
    expect(headerButton('P/E').closest('th')?.getAttribute('aria-sort')).toBe('descending');
    expect(page().textContent).toContain('1 of 3 have no P/E value and are listed last.');

    headerButton('P/E').click();
    await settle();

    expect(shownTickers()).toEqual(['AAA', 'CCC', 'BBB']);
    expect(headerButton('P/E').closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    expect(headerButton('Company').closest('th')?.getAttribute('aria-sort')).toBe('none');
  });

  // The page has two pickers; each test names the one it means.
  function picker(name: 'sectors' | 'columns'): HTMLElement {
    const element = page().querySelector<HTMLElement>(`[data-picker="${name}"]`);
    if (!element) {
      throw new Error(`No picker ${name}`);
    }
    return element;
  }

  function pickerBox(name: 'sectors' | 'columns', label: string): HTMLInputElement {
    const rows = [
      ...picker(name).querySelectorAll<HTMLLabelElement>('.picker-panel fieldset .check-row'),
    ];
    const row = rows.find(
      (candidate) => candidate.querySelector('.check-label')?.textContent === label,
    );
    const box = row?.querySelector<HTMLInputElement>('input');
    if (!box) {
      throw new Error(`No option ${label} in ${name}`);
    }
    return box;
  }

  function sectorBox(name: string): HTMLInputElement {
    return pickerBox('sectors', name);
  }

  async function clickAndSettle(element: HTMLElement): Promise<void> {
    element.click();
    await settle();
  }

  function pickerSummaryText(name: 'sectors' | 'columns', prefix: string): string {
    return (
      picker(name).querySelector('.picker-summary')?.textContent?.replace(prefix, '').trim() ?? ''
    );
  }

  function sectorSummaryText(): string {
    return pickerSummaryText('sectors', 'Sector:');
  }

  function shownHeaders(): string[] {
    const headers = page().querySelectorAll('thead th .sort-button');
    return [...headers].map((button) => button.textContent?.replace(/\s+/g, ' ').trim() ?? '');
  }

  it('filters by sector without a new request and counts within the sector', async () => {
    await open();
    headerButton('P/E').click();
    await settle();

    await clickAndSettle(sectorBox('Energy'));

    expect(shownTickers()).toEqual(['AAA', 'BBB']);
    expect(sectorSummaryText()).toBe('Energy');
    expect(page().textContent).toContain('2 of 3 companies match');
    expect(page().textContent).toContain('1 of 2 have no P/E value');

    await clickAndSettle(sectorBox('Energy'));
    expect(shownTickers()).toEqual(['CCC', 'AAA', 'BBB']);
    expect(sectorSummaryText()).toBe('All sectors');
  });

  it('combines several sectors, and excludes them on request', async () => {
    await open();

    await clickAndSettle(sectorBox('Energy'));
    await clickAndSettle(sectorBox('Technology'));
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(sectorSummaryText()).toBe('2 sectors');

    await clickAndSettle(sectorBox('Technology'));
    const exclude = picker('sectors').querySelector<HTMLInputElement>('.picker-actions input')!;
    await clickAndSettle(exclude);

    expect(shownTickers()).toEqual(['CCC']);
    expect(sectorSummaryText()).toBe('All except Energy');
    expect(TestBed.inject(Router).url).toContain('exclude=1');

    await clickAndSettle(picker('sectors').querySelector<HTMLButtonElement>('.reset-button')!);
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(TestBed.inject(Router).url).not.toContain('sector');
  });

  it('offers each sector once, sorted, with its number of companies', async () => {
    await open();

    const rows = picker('sectors').querySelectorAll('.picker-panel fieldset .check-row');
    const texts = [...rows].map((row) => [
      row.querySelector('.check-label')?.textContent,
      row.querySelector('.option-count')?.textContent,
    ]);
    expect(texts).toEqual([
      ['Energy', '2'],
      ['Technology', '1'],
    ]);
  });

  it('closes the sector panel on a press outside it, and on Escape', async () => {
    await open();
    const details = picker('sectors').querySelector<HTMLDetailsElement>('details')!;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    await settle();

    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    await settle();
    expect(details.open).toBe(false);

    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    await settle();
    details.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await settle();
    expect(details.open).toBe(false);
    expect(document.activeElement?.classList.contains('picker-summary')).toBe(true);
  });

  it('closes the sector panel when Tab moves the focus out of it', async () => {
    // W35: the panel stayed open over the page while the focus was already on the next control.
    await open();
    const host = picker('sectors');
    const details = host.querySelector<HTMLDetailsElement>('details')!;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    await settle();
    const firstBox = host.querySelector<HTMLInputElement>('.check-box')!;
    const outside = page().querySelector<HTMLSelectElement>('#add-criterion')!;

    // Within the panel, or with no element to go to (a click on its padding), it stays open.
    firstBox.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: host }));
    firstBox.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
    await settle();
    expect(details.open).toBe(true);

    firstBox.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: outside }));
    await settle();
    expect(details.open).toBe(false);
  });

  it('restores date, sector and sort from the address', async () => {
    const response = { ...THREE_COMPANIES, valuation_date: '2021-03-31' };
    await open('/screener?date=2021-03-31&sector=Energy&sort=pe&dir=asc', response);

    expect(shownTickers()).toEqual(['AAA', 'BBB']);
    expect(page().querySelector<HTMLInputElement>('#valuation-date')?.value).toBe('2021-03-31');
    expect(sectorBox('Energy').checked).toBe(true);
    expect(page().textContent).toContain('valued at 31 Mar 2021');
  });

  it('finds companies by ticker or name and offers to clear a search without results', async () => {
    await open('/screener?q=ccc');

    expect(page().querySelector<HTMLInputElement>('#company-search')?.value).toBe('ccc');
    expect(shownTickers()).toEqual(['CCC']);
    expect(page().textContent).toContain('1 of 3 companies match');

    const search = page().querySelector<HTMLInputElement>('#company-search')!;
    search.value = 'nothing';
    search.dispatchEvent(new Event('input'));
    await settle();

    expect(page().textContent).toContain('No company matches "nothing".');
    // The search box stays, so the search can be changed.
    expect(page().querySelector('#company-search')).not.toBeNull();

    const clear = [...page().querySelectorAll<HTMLButtonElement>('app-state-message button')];
    await clickAndSettle(clear[0]);
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(TestBed.inject(Router).url).not.toContain('q=');
  });

  it('downloads the shown rows in the shown order as CSV', async () => {
    await open('/screener?sort=pe&dir=desc');
    const created: Blob[] = [];
    const createObjectURL = vi.fn((file: Blob) => {
      created.push(file);
      return 'blob:test';
    });
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this);
    });

    const buttons = [...page().querySelectorAll<HTMLButtonElement>('.results-toolbar button')];
    buttons.find((button) => button.textContent?.includes('CSV'))!.click();

    expect(clicked[0].download).toBe('screener-2025-12-31.csv');
    const text = await created[0].text();
    const tickers = text
      .replace(String.fromCharCode(0xfeff), '')
      .split('\r\n')
      .slice(1, -1)
      .map((line) => line.split(',')[1]);
    expect(tickers).toEqual(['CCC', 'AAA', 'BBB']);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the median and middle 80% of a criterion for the chosen sectors', async () => {
    await open('/screener?filter=roe::');

    function statsTexts(): string[] {
      const stats = page().querySelectorAll('.criterion-stats span');
      return [...stats].map((span) => span.textContent?.replace(/\s+/g, ' ').trim() ?? '');
    }
    const statsTitle = () => page().querySelector('.criterion-stats')?.getAttribute('title');

    // ROE of AAA, BBB, CCC: 20%, 0%, 10%. Sorted 0, 10, 20; median by nearest rank is 10%.
    expect(statsTexts()).toEqual(['Median 10.0%', '80% between 0.0% and 20.0%']);
    expect(statsTitle()).toBe('Based on 3 companies with a value');

    await clickAndSettle(sectorBox('Energy'));
    // Energy has 20% and 0%: rank ceil(1) = 1 gives 0%, rank ceil(1.8) = 2 gives 20%.
    expect(statsTexts()[0]).toBe('Median 0.0%');
    expect(statsTitle()).toBe('Based on 2 companies with a value');
  });

  it('hides a column from the table and the address, and shows it again', async () => {
    await open();
    expect(shownHeaders()).toHaveLength(16);
    expect(pickerSummaryText('columns', 'Columns:')).toBe('Columns');

    await clickAndSettle(pickerBox('columns', 'P/E'));
    await clickAndSettle(pickerBox('columns', 'Price'));

    expect(shownHeaders()).not.toContain('P/E');
    expect(shownHeaders()).not.toContain('Price');
    expect(shownHeaders()).toHaveLength(14);
    // Every row has a cell for each header except the company column.
    expect(page().querySelectorAll('tbody tr')[0].querySelectorAll('td')).toHaveLength(13);
    expect(pickerBox('columns', 'P/E').checked).toBe(false);
    expect(pickerSummaryText('columns', 'Columns:')).toBe('Columns (13 of 15)');
    expect(TestBed.inject(Router).url).toContain('hide=price,pe');

    await clickAndSettle(picker('columns').querySelector<HTMLButtonElement>('.reset-button')!);

    expect(shownHeaders()).toHaveLength(16);
    expect(TestBed.inject(Router).url).not.toContain('hide');
  });

  it('keeps criteria and the CSV on a hidden column', async () => {
    // A criterion that the reader set is not forgotten because the column is not shown.
    await open('/screener?filter=pe::20&hide=pe');

    expect(shownHeaders()).not.toContain('P/E');
    expect(shownTickers()).toEqual(['AAA']);

    const created: Blob[] = [];
    vi.stubGlobal(
      'URL',
      Object.assign(URL, {
        createObjectURL: vi.fn((file: Blob) => {
          created.push(file);
          return 'blob:test';
        }),
        revokeObjectURL: vi.fn(),
      }),
    );
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    const download = [...page().querySelectorAll<HTMLButtonElement>('.results-toolbar button')];
    download.find((button) => button.textContent?.includes('CSV'))!.click();

    const header = (await created[0].text()).split('\r\n')[0];
    expect(header).toContain(',pe,');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('returns the order to the default when the sorted column is hidden', async () => {
    await open('/screener?sort=pe&dir=desc');
    expect(shownTickers()).toEqual(['CCC', 'AAA', 'BBB']);

    await clickAndSettle(pickerBox('columns', 'P/E'));

    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(TestBed.inject(Router).url).not.toContain('sort=');
  });

  it('keeps full column names for assistive technology behind shortened headers', async () => {
    await open();

    const header = headerButton('Op. margin');
    expect(header.title).toBe('Operating margin');
    expect(header.querySelector('.visually-hidden')?.textContent).toBe('Operating margin');
  });

  it('hides the old result at once when the date changes, until the new one arrives', async () => {
    // Principle 3: an old result must not look like the answer to the new question.
    await open();

    await changeControl('#valuation-date', '2021-03-31');

    expect(shownTickers()).toEqual([]);
    expect(page().querySelector('[role="status"]')?.textContent).toContain('31 Mar 2021');
    expect(TestBed.inject(Router).url).toContain('date=2021-03-31');

    const response: ScreenerResponse = {
      valuation_date: '2021-03-31',
      rows: [company('NEW', 'Energy', {})],
    };
    http.expectOne(`${API}?date=2021-03-31`).flush(response);
    harness.detectChanges();

    expect(shownTickers()).toEqual(['NEW']);
  });

  it('ignores the response of an earlier date when a later one was chosen', async () => {
    await open();
    await changeControl('#valuation-date', '2021-03-31');
    const firstRequest = http.expectOne(`${API}?date=2021-03-31`);

    await changeControl('#valuation-date', '2022-03-31');

    expect(firstRequest.cancelled).toBe(true);
    const response: ScreenerResponse = {
      valuation_date: '2022-03-31',
      rows: [company('LATER', 'Energy', {})],
    };
    http.expectOne(`${API}?date=2022-03-31`).flush(response);
    harness.detectChanges();

    expect(shownTickers()).toEqual(['LATER']);
  });

  it('sends no request for an invalid date and keeps the table with its own date', async () => {
    await open();

    await changeControl('#valuation-date', '2010-01-01');

    http.expectNone(`${API}?date=2010-01-01`);
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Choose a date from 31 Mar 2016 to 31 Dec 2025',
    );
    expect(page().querySelector('#valuation-date')?.getAttribute('aria-invalid')).toBe('true');
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(page().textContent).toContain('valued at 31 Dec 2025');

    await changeControl('#valuation-date', '2025-12-31');
    expect(page().querySelector('[role="alert"]')).toBeNull();
  });

  it('applies a typed date on Enter or on leaving the field, not on each part of it', async () => {
    // W32: Chrome reports every finished part of a typed date as a change. Each one loaded a
    // date nobody chose, hid the table, and the half-typed year (0002) flashed an error.
    await open();
    const input = page().querySelector<HTMLInputElement>('#valuation-date')!;
    const typeKey = (key: string) =>
      input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));

    typeKey('2');
    await changeControl('#valuation-date', '0002-03-31');
    typeKey('0');
    await changeControl('#valuation-date', '2020-03-31');

    http.expectNone(() => true);
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);

    input.dispatchEvent(new Event('blur'));
    await settle();
    http
      .expectOne(`${API}?date=2020-03-31`)
      .flush({ ...THREE_COMPANIES, valuation_date: '2020-03-31' });
    harness.detectChanges();
    expect(page().textContent).toContain('valued at 31 Mar 2020');

    typeKey('1');
    await changeControl('#valuation-date', '2021-03-31');
    http.expectNone(() => true);
    typeKey('Enter');
    await settle();
    http.expectOne(`${API}?date=2021-03-31`).flush(THREE_COMPANIES);
  });

  it('shows the range error for a typed date only when the field is left', async () => {
    await open();
    const input = page().querySelector<HTMLInputElement>('#valuation-date')!;

    input.dispatchEvent(new KeyboardEvent('keydown', { key: '0', bubbles: true }));
    await changeControl('#valuation-date', '');
    expect(page().querySelector('[role="alert"]')).toBeNull();

    input.dispatchEvent(new Event('blur'));
    await settle();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain('Choose a date from');
  });

  it('says so when the chosen day had no session', async () => {
    const holiday: ScreenerResponse = {
      valuation_date: '2021-01-01',
      rows: [
        {
          ...company('AAA', 'Energy', {}),
          latest: { ...company('AAA', 'Energy', {}).latest!, price_date: '2020-12-31' },
        },
      ],
    };

    await open('/screener?date=2021-01-01', holiday);

    expect(page().textContent).toContain('No trading that day: prices from 31 Dec 2020.');
  });

  it('shows an error with a retry that loads the same date again', async () => {
    harness = await RouterTestingHarness.create('/screener?date=2021-03-31');
    http
      .expectOne(`${API}?date=2021-03-31`)
      .flush({}, { status: 503, statusText: 'Service Unavailable' });
    harness.detectChanges();

    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Failed to load the screener.',
    );

    page().querySelector<HTMLButtonElement>('app-state-message .retry-button')?.click();
    harness.detectChanges();
    expect(page().querySelector('[role="status"]')?.textContent).toContain('Loading');

    http.expectOne(`${API}?date=2021-03-31`).flush(THREE_COMPANIES);
    harness.detectChanges();
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
  });

  it('explains a sector without companies and offers to clear the filter', async () => {
    await open('/screener?sector=Unknown');

    expect(page().textContent).toContain('No companies in the selected sectors.');
    expect(page().querySelector('table')).toBeNull();
    // The unknown sector stays visible in the control, with no companies.
    expect(sectorBox('Unknown').checked).toBe(true);

    page().querySelector<HTMLButtonElement>('app-state-message .action-button')?.click();
    await settle();

    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
  });

  function criterionInput(label: string, bound: 'minimum' | 'maximum'): HTMLInputElement {
    const input = page().querySelector<HTMLInputElement>(`input[aria-label="${label} ${bound}"]`);
    if (!input) {
      throw new Error(`No input ${label} ${bound}`);
    }
    return input;
  }

  async function typeInto(input: HTMLInputElement, text: string): Promise<void> {
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await settle();
  }

  it('adds a criterion from the list, focuses its minimum and removes it from the list', async () => {
    await open();
    expect(page().querySelector('.criterion')).toBeNull();

    await changeControl('#add-criterion', 'pe');

    expect(page().querySelectorAll('.criterion')).toHaveLength(1);
    expect(page().querySelector('.criterion-label')?.textContent).toBe('P/E');
    expect(document.activeElement?.id).toBe('criterion-min-pe');
    // Offered once only, and the select returns to its prompt.
    const options = [...page().querySelectorAll('#add-criterion option')];
    expect(options.map((option) => option.textContent)).not.toContain('P/E');
    expect(page().querySelector<HTMLSelectElement>('#add-criterion')?.value).toBe('');
  });

  it('waits for Enter before adding a criterion browsed to with the arrow keys', async () => {
    // W31: Chrome on Windows reports each arrow key on a closed list as a change. Adding there
    // moved the focus to the new field while the reader was still browsing (WCAG 3.2.2).
    await open();
    const select = page().querySelector<HTMLSelectElement>('#add-criterion')!;

    select.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await changeControl('#add-criterion', 'pe');

    expect(page().querySelector('.criterion')).toBeNull();
    expect(select.value).toBe('pe');

    select.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await settle();

    expect(page().querySelector('.criterion-label')?.textContent).toBe('P/E');
    expect(document.activeElement?.id).toBe('criterion-min-pe');
  });

  it('adds nothing when the reader browses the list and leaves it without Enter', async () => {
    await open();
    const select = page().querySelector<HTMLSelectElement>('#add-criterion')!;

    select.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await changeControl('#add-criterion', 'roe');
    select.dispatchEvent(new Event('blur'));
    await settle();

    expect(page().querySelector('.criterion')).toBeNull();
    expect(select.value).toBe('');
  });

  it('adds a criterion picked with the pointer at once, also after keyboard use', async () => {
    await open();
    const select = page().querySelector<HTMLSelectElement>('#add-criterion')!;
    select.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));

    select.dispatchEvent(new Event('pointerdown'));
    await changeControl('#add-criterion', 'roe');

    expect(page().querySelector('.criterion-label')?.textContent).toBe('ROE');
  });

  it('filters by a typed range, counts the result and names who was left out', async () => {
    await open();
    await changeControl('#add-criterion', 'pe');

    await typeInto(criterionInput('P/E', 'maximum'), '20');

    // AAA has P/E 10, CCC has 30, and BBB has no value at all.
    expect(shownTickers()).toEqual(['AAA']);
    expect(page().textContent).toContain('1 of 3 companies match, valued at');
    expect(page().querySelector('.criterion-notes')?.textContent).toContain(
      '1 company has no P/E value and is left out.',
    );

    await typeInto(criterionInput('P/E', 'maximum'), '');
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(page().querySelector('.criterion-notes')).toBeTruthy();
    expect(page().querySelector('.criterion-notes')?.textContent?.trim()).toBe('');
  });

  it('treats a percent criterion as percent and combines it with the sector', async () => {
    await open();
    await changeControl('#add-criterion', 'roe');

    await typeInto(criterionInput('ROE', 'minimum'), '15');
    expect(shownTickers()).toEqual(['AAA']);

    await clickAndSettle(sectorBox('Technology'));
    // CCC has ROE 10%, below the minimum, so the sector leaves nothing.
    expect(page().textContent).toContain('No companies match these criteria.');
  });

  it('flags a typed value that is not a number without applying it', async () => {
    await open();
    await changeControl('#add-criterion', 'pe');

    await typeInto(criterionInput('P/E', 'minimum'), 'abc');

    expect(criterionInput('P/E', 'minimum').getAttribute('aria-invalid')).toBe('true');
    expect(page().querySelector('.criterion-notes')?.textContent).toContain('plain number');
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
  });

  it('keeps the typed text unchanged while the address follows', async () => {
    // A number field would turn "1." into "1" when the address is written back.
    await open();
    await changeControl('#add-criterion', 'pe');

    await typeInto(criterionInput('P/E', 'maximum'), '12.');

    expect(criterionInput('P/E', 'maximum').value).toBe('12.');
    expect(TestBed.inject(Router).url).toContain('filter=pe::12.');
  });

  it('restores criteria from the address and clears them with one button', async () => {
    await open('/screener?filter=pe:5:20&filter=roe:15:');

    expect(page().querySelectorAll('.criterion')).toHaveLength(2);
    expect(criterionInput('P/E', 'maximum').value).toBe('20');
    expect(criterionInput('ROE', 'minimum').value).toBe('15');
    expect(shownTickers()).toEqual(['AAA']);

    const clear = [...page().querySelectorAll<HTMLButtonElement>('.criteria-header button')];
    clear[0].click();
    await settle();

    expect(page().querySelector('.criterion')).toBeNull();
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    expect(TestBed.inject(Router).url).not.toContain('filter');
  });

  it('removes a single criterion and offers it again', async () => {
    await open('/screener?filter=pe::20');
    expect(shownTickers()).toEqual(['AAA']);

    page().querySelector<HTMLButtonElement>('.remove-button')?.click();
    await settle();

    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
    const options = [...page().querySelectorAll('#add-criterion option')];
    expect(options.map((option) => option.textContent)).toContain('P/E');
  });

  it('keeps both choices when two sectors are ticked before the address updates', async () => {
    // Regression test (N86): the second click used to read the state before the first one.
    await open();

    sectorBox('Energy').click();
    sectorBox('Technology').click();
    await settle();

    expect(sectorSummaryText()).toBe('2 sectors');
    expect(TestBed.inject(Router).url).toContain('sector=Energy&sector=Technology');
  });

  it('starts afresh when the Screener link is followed while the page is open', async () => {
    // Regression test: the address lost the criteria, but the page kept filtering with them.
    await open('/screener?filter=pe::20&q=a');
    expect(shownTickers()).toEqual(['AAA']);

    await harness.navigateByUrl('/screener');
    harness.detectChanges();

    expect(page().querySelector('.criterion')).toBeNull();
    expect(page().querySelector<HTMLInputElement>('#company-search')?.value).toBe('');
    expect(shownTickers()).toEqual(['AAA', 'BBB', 'CCC']);
  });

  it('explains an empty answer from the API', async () => {
    await open('/screener', { valuation_date: '2025-12-31', rows: [] });

    expect(page().textContent).toContain('No companies are available for this date.');
    expect(page().querySelector('table')).toBeNull();
  });
});
