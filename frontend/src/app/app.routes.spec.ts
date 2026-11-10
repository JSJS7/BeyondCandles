import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Title } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { routes } from './app.routes';

describe('Page titles', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
    });
  });

  it('names each page in the browser tab, the company page by its ticker until it loads', async () => {
    // WCAG 2.4.2: a tab that always says "BeyondCandles" does not tell the pages apart.
    // The company page replaces the ticker with the company's name once it has loaded.
    const harness = await RouterTestingHarness.create();
    const title = TestBed.inject(Title);

    await harness.navigateByUrl('/');
    expect(title.getTitle()).toBe('Dashboard · BeyondCandles');

    await harness.navigateByUrl('/screener');
    expect(title.getTitle()).toBe('Screener · BeyondCandles');

    await harness.navigateByUrl('/stocks/AAPL');
    expect(title.getTitle()).toBe('AAPL · BeyondCandles');

    await harness.navigateByUrl('/no-such-page');
    expect(title.getTitle()).toBe('Page not found · BeyondCandles');
  });
});
