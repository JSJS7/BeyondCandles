import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter, Router } from '@angular/router';
import { TopbarComponent } from './topbar';

describe('Topbar', () => {
  const catalogueUrl = 'http://127.0.0.1:8000/stocks';
  const catalogue = [
    { ticker: 'AAPL', name: 'Apple Inc.', exchange: 'NMS', sector: 'Technology' },
    ...Array.from({ length: 12 }, (_, index) => ({
      ticker: `T${index}`,
      name: `Example company ${index}`,
      exchange: 'NYQ',
      sector: null,
    })),
  ];
  let component: TopbarComponent;
  let fixture: ComponentFixture<TopbarComponent>;
  let http: HttpTestingController;

  beforeEach(async () => {
    localStorage.removeItem('beyondcandles-theme');
    await TestBed.configureTestingModule({
      imports: [TopbarComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(TopbarComponent);
    component = fixture.componentInstance;
    http = TestBed.inject(HttpTestingController);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    fixture.detectChanges();
  });

  afterEach(() => {
    http.verify();
    vi.restoreAllMocks();
    localStorage.removeItem('beyondcandles-theme');
    document.documentElement.removeAttribute('data-theme');
  });

  function failCatalogue(): void {
    http.expectOne(catalogueUrl).flush({}, { status: 503, statusText: 'Service Unavailable' });
    fixture.detectChanges();
  }

  it('searches ticker and company name without case sensitivity', () => {
    http.expectOne(catalogueUrl).flush(catalogue);
    component.onSearchInput(' aApL ');
    expect(component.filteredStocks().map((stock) => stock.ticker)).toEqual(['AAPL']);
    component.onSearchInput('APPLE');
    expect(component.filteredStocks().map((stock) => stock.ticker)).toEqual(['AAPL']);
  });

  it('limits suggestions to ten and clears results for empty or unmatched queries', () => {
    http.expectOne(catalogueUrl).flush(catalogue);
    component.onSearchInput('example');
    expect(component.filteredStocks()).toHaveLength(10);
    component.onSearchInput('unknown');
    expect(component.filteredStocks()).toEqual([]);
    component.onSearchInput(' ');
    expect(component.filteredStocks()).toEqual([]);
  });

  it('lists an exact ticker first, then ticker starts, name words and other matches', () => {
    // W14: one-letter tickers (V, T) were buried under alphabetical substring matches.
    http.expectOne(catalogueUrl).flush([
      { ticker: 'ADSK', name: 'Autodesk, Inc.', exchange: 'NMS', sector: null },
      { ticker: 'BA', name: 'The Boeing Company', exchange: 'NYQ', sector: null },
      { ticker: 'MA', name: 'Mastercard Incorporated', exchange: 'NYQ', sector: null },
      { ticker: 'NVDA', name: 'NVIDIA Corporation', exchange: 'NMS', sector: null },
      { ticker: 'T', name: 'AT&T Inc.', exchange: 'NYQ', sector: null },
      { ticker: 'TGT', name: 'Target Corporation', exchange: 'NYQ', sector: null },
      { ticker: 'V', name: 'Visa Inc.', exchange: 'NYQ', sector: null },
      { ticker: 'VZ', name: 'Verizon Communications Inc.', exchange: 'NYQ', sector: null },
    ]);

    component.onSearchInput('v');
    expect(component.filteredStocks().map((stock) => stock.ticker)).toEqual(['V', 'VZ', 'NVDA']);

    // "The Boeing Company" has a word starting with "t"; the rest only contain the letter.
    component.onSearchInput('T');
    expect(component.filteredStocks().map((stock) => stock.ticker)).toEqual([
      'T',
      'TGT',
      'BA',
      'ADSK',
      'MA',
      'NVDA',
      'VZ',
    ]);
  });

  it('recovers through Try again while keeping the entered query', () => {
    const input: HTMLInputElement = fixture.nativeElement.querySelector('input');
    input.value = 'aapl';
    input.dispatchEvent(new Event('input'));
    failCatalogue();

    expect(fixture.nativeElement.textContent).toContain('Companies are temporarily unavailable.');
    const retryButton: HTMLButtonElement = fixture.nativeElement.querySelector('.retry-button');
    expect(retryButton.textContent).toContain('Try again');
    retryButton.click();
    fixture.detectChanges();

    const loading = fixture.nativeElement.querySelector('.search-loading');
    expect(loading.textContent).toContain('Loading companies');
    expect(loading.querySelectorAll('.loading-dots span')).toHaveLength(3);
    expect(loading.querySelector('.loading-dots').getAttribute('aria-hidden')).toBe('true');
    expect(component.searchQuery()).toBe('aapl');
    http.expectOne(catalogueUrl).flush(catalogue);
    fixture.detectChanges();

    expect(input.value).toBe('aapl');
    expect(fixture.nativeElement.querySelector('.search-result').textContent).toContain('AAPL');
    expect(fixture.nativeElement.querySelector('.retry-button')).toBeNull();
    expect(fixture.nativeElement.textContent).not.toContain('temporarily unavailable');
  });

  it('retries on focus after failure without sending overlapping requests', () => {
    component.onSearchInput('apple');
    failCatalogue();
    const input: HTMLInputElement = fixture.nativeElement.querySelector('input');

    input.dispatchEvent(new Event('focus'));
    input.dispatchEvent(new Event('focus'));
    component.loadStocks();
    // Multiple triggers during loading must still produce exactly one request.
    http.expectOne(catalogueUrl).flush(catalogue);
    fixture.detectChanges();
    expect(component.filteredStocks().map((stock) => stock.ticker)).toEqual(['AAPL']);

    input.dispatchEvent(new Event('focus'));
    http.expectNone(catalogueUrl);
  });

  it('keeps the retry option available if the next request also fails', () => {
    component.onSearchInput('aapl');
    failCatalogue();
    fixture.nativeElement.querySelector('.retry-button').click();
    failCatalogue();

    expect(component.stocksLoadState().status).toBe('error');
    expect(component.searchQuery()).toBe('aapl');
    expect(fixture.nativeElement.querySelector('.retry-button')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('.search-result')).toBeNull();
  });

  it('cancels an unfinished catalogue request when the component is destroyed', () => {
    const request = http.expectOne(catalogueUrl);
    fixture.destroy();
    expect(request.cancelled).toBe(true);
  });

  it('dismisses suggestions with Escape and reopens them without losing the query or reloading', () => {
    http.expectOne(catalogueUrl).flush(catalogue);
    const input: HTMLInputElement = fixture.nativeElement.querySelector('input');
    input.value = 'aapl';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.search-results')).not.toBeNull();

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.search-results')).toBeNull();
    expect(input.value).toBe('aapl');

    // Returning to the input reuses the loaded catalogue and preserves the search text.
    input.dispatchEvent(new Event('focus'));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.search-result').textContent).toContain('AAPL');
    http.expectNone(catalogueUrl);

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    input.value = 'apple';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.search-result').textContent).toContain('AAPL');
  });

  function openedSearch(query: string): HTMLInputElement {
    http.expectOne(catalogueUrl).flush(catalogue);
    const input: HTMLInputElement = fixture.nativeElement.querySelector('input');
    input.value = query;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    return input;
  }

  function press(input: HTMLInputElement, key: string): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    input.dispatchEvent(event);
    fixture.detectChanges();
    return event;
  }

  it('closes suggestions on a click outside the search, not on one inside it', () => {
    // N31: only Escape or a choice closed the list.
    openedSearch('aapl');
    const results = fixture.nativeElement.querySelector('.search-results');

    results.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.search-results')).not.toBeNull();

    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.search-results')).toBeNull();
    expect(component.searchQuery()).toBe('aapl');
  });

  it('closes suggestions when Tab leaves the search', () => {
    const input = openedSearch('aapl');

    press(input, 'Tab');

    expect(fixture.nativeElement.querySelector('.search-results')).toBeNull();
  });

  it('marks options with the arrow keys, wrapping at both ends, without moving focus', () => {
    const input = openedSearch('example');
    expect(input.getAttribute('aria-activedescendant')).toBeNull();

    const event = press(input, 'ArrowDown');
    expect(event.defaultPrevented).toBe(true);
    expect(input.getAttribute('aria-activedescendant')).toBe('search-option-0');
    const first = fixture.nativeElement.querySelector('#search-option-0');
    expect(first.getAttribute('aria-selected')).toBe('true');

    press(input, 'ArrowUp');
    expect(input.getAttribute('aria-activedescendant')).toBe('search-option-9');
    press(input, 'ArrowDown');
    expect(input.getAttribute('aria-activedescendant')).toBe('search-option-0');
  });

  it('opens the marked option with Enter, or the best match when none is marked', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const input = openedSearch('example');

    press(input, 'ArrowDown');
    press(input, 'ArrowDown');
    press(input, 'Enter');
    expect(navigate).toHaveBeenCalledWith(['/stocks', 'T1']);
    expect(component.searchQuery()).toBe('');

    input.value = 'apple';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    press(input, 'Enter');
    expect(navigate).toHaveBeenLastCalledWith(['/stocks', 'AAPL']);
  });

  it('does nothing on Enter when no option is shown', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const input = openedSearch('unknown');

    const event = press(input, 'Enter');

    expect(navigate).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it('exposes the combobox state to assistive technology', () => {
    const input = openedSearch('aapl');

    expect(input.getAttribute('role')).toBe('combobox');
    expect(input.getAttribute('aria-expanded')).toBe('true');
    expect(fixture.nativeElement.querySelector('#search-options').getAttribute('role')).toBe(
      'listbox',
    );
    expect(fixture.nativeElement.querySelector('.search-result').getAttribute('role')).toBe(
      'option',
    );

    press(input, 'Escape');
    expect(input.getAttribute('aria-expanded')).toBe('false');
  });

  it('switches the page theme using the labelled toolbar button', () => {
    http.expectOne(catalogueUrl).flush(catalogue);
    const previousTheme = component.theme.mode();
    const button: HTMLButtonElement = fixture.nativeElement.querySelector('.icon-button');
    button.click();
    fixture.detectChanges();

    const expectedTheme = previousTheme === 'light' ? 'dark' : 'light';
    expect(document.documentElement.dataset['theme']).toBe(expectedTheme);
    expect(localStorage.getItem('beyondcandles-theme')).toBe(expectedTheme);
    expect(button.getAttribute('aria-label')).toBe(`Switch to ${previousTheme} mode`);
  });
});
