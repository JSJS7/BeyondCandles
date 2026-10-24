import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Price } from '../../models/stock';
import { PriceChart } from './price-chart';
import { PriceChartRenderer, PriceLineChart } from './price-chart-renderer';

// Canvas rendering is checked in the browser; these tests exercise selection behavior.

function makePrices(): Price[] {
  const prices: Price[] = [];
  const day = new Date('2024-01-02T00:00:00Z');
  while (prices.length < 40) {
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) {
      prices.push({
        date: day.toISOString().slice(0, 10),
        open: '100',
        high: '200',
        low: '90',
        close: '100',
        adjusted_close: String(100 + prices.length),
        volume: 1000,
      });
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return prices;
}

describe('Price chart interaction', () => {
  const renderer = { create: vi.fn() };
  let fixture: ComponentFixture<PriceChart>;
  let component: PriceChart;
  let canvas: HTMLCanvasElement;
  let prices: Price[];

  beforeEach(async () => {
    renderer.create.mockImplementation(
      (_canvas, visible) =>
        ({
          width: 900,
          height: 340,
          chartArea: { left: 50, right: 850, top: 20, bottom: 300 },
          scales: {
            x: {
              getValueForPixel: (x: number) => ((x - 50) / 800) * (visible.length - 1),
              getPixelForValue: (index: number) => 50 + (index / (visible.length - 1)) * 800,
            },
          },
          options: { plugins: { tooltip: { enabled: true } } },
          update: vi.fn(),
          destroy: vi.fn(),
        }) as unknown as PriceLineChart,
    );
    await TestBed.configureTestingModule({
      imports: [PriceChart],
      providers: [{ provide: PriceChartRenderer, useValue: renderer }],
    }).compileComponents();
    fixture = TestBed.createComponent(PriceChart);
    component = fixture.componentInstance;
    prices = makePrices();
    fixture.componentRef.setInput('prices', prices);
    fixture.detectChanges();
    canvas = fixture.nativeElement.querySelector('canvas');
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      width: 900,
      height: 340,
      right: 900,
      bottom: 340,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    canvas.setPointerCapture = vi.fn();
    canvas.hasPointerCapture = vi.fn(() => true);
    canvas.releasePointerCapture = vi.fn();
  });

  afterEach(() => {
    fixture.destroy();
    vi.clearAllMocks();
    localStorage.removeItem('beyondcandles-theme');
    document.documentElement.removeAttribute('data-theme');
  });

  function pointer(type: string, x: number, pointerType = 'mouse'): void {
    canvas.dispatchEvent(
      new PointerEvent(type, {
        pointerId: 1,
        pointerType,
        isPrimary: true,
        button: 0,
        clientX: x,
        clientY: 100,
        bubbles: true,
      }),
    );
    fixture.detectChanges();
  }

  function xAt(index: number): number {
    return 50 + (index / (component.visiblePrices().length - 1)) * 800;
  }

  it.each([
    [3, 9],
    [9, 3],
  ])('zooms an inclusive seven-session selection from %i to %i', (start, end) => {
    const startX = xAt(start);
    const endX = xAt(end);
    pointer('pointerdown', startX);
    pointer('pointermove', endX);
    expect(component.selectionBox()).not.toBeNull();
    pointer('pointerup', endX);
    expect(component.visiblePrices()).toEqual(prices.slice(3, 10));
    expect(component.selectedPreset()).toBe('Custom');
    expect(component.changeLabel()).toBe('+5.83%');
    expect(component.selectionBox()).toBeNull();
  });

  it('allows two adjacent sessions without the previous seven-session minimum', () => {
    pointer('pointerdown', xAt(0));
    pointer('pointerup', xAt(1));
    expect(component.message()).toBe('');
    expect(component.visiblePrices()).toEqual(prices.slice(0, 2));
  });

  it('pins the first clicked day and clamps releases outside the chart', () => {
    pointer('pointerdown', xAt(5));
    pointer('pointerup', xAt(5));
    expect(component.selectedPreset()).toBe('All');
    pointer('pointerdown', xAt(5));
    pointer('pointermove', 1000);
    pointer('pointerup', 1000);
    expect(component.visiblePrices()).toEqual(prices.slice(5));
    expect(canvas.releasePointerCapture).toHaveBeenCalled();
  });

  it('allows successive zooms and restores the entire source with All', () => {
    pointer('pointerdown', xAt(0));
    pointer('pointerup', xAt(19));
    expect(component.visiblePrices()).toHaveLength(20);
    pointer('pointerdown', xAt(2));
    pointer('pointerup', xAt(8));
    expect(component.visiblePrices()).toEqual(prices.slice(2, 9));
    component.choosePreset('All');
    expect(component.visiblePrices()).toEqual(prices);
    expect(component.selectedPreset()).toBe('All');
  });

  it('requires explicit touch selection and exits that mode after a valid gesture', () => {
    pointer('pointerdown', xAt(0), 'touch');
    pointer('pointerup', xAt(10), 'touch');
    expect(component.selectedPreset()).toBe('All');
    component.toggleRangeSelection();
    fixture.detectChanges();
    pointer('pointerdown', xAt(0), 'touch');
    pointer('pointerup', xAt(10), 'touch');
    expect(component.visiblePrices()).toEqual(prices.slice(0, 11));
    expect(component.selectingRange()).toBe(false);
  });

  it('cancels with Escape or pointer cancellation without changing the range', () => {
    component.toggleRangeSelection();
    pointer('pointerdown', xAt(0));
    pointer('pointermove', xAt(10));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    fixture.detectChanges();
    pointer('pointerup', xAt(10));
    expect(component.selectionBox()).toBeNull();
    expect(component.selectingRange()).toBe(false);
    expect(component.visiblePrices()).toEqual(prices);

    pointer('pointerdown', xAt(0));
    pointer('pointermove', xAt(10));
    pointer('pointercancel', xAt(10));
    pointer('pointerup', xAt(10));
    expect(component.visiblePrices()).toEqual(prices);
  });

  it('applies typed dates and keeps the previous range when both dates match one session', () => {
    fixture.nativeElement.querySelector('.select-range').click();
    fixture.detectChanges();
    const inputs: NodeListOf<HTMLInputElement> = fixture.nativeElement.querySelectorAll('input');
    inputs[0].value = prices[2].date;
    inputs[1].value = prices[8].date;
    inputs.forEach((input) => input.dispatchEvent(new Event('input')));
    fixture.nativeElement
      .querySelector('form')
      .dispatchEvent(new Event('submit', { cancelable: true }));
    fixture.detectChanges();
    expect(component.visiblePrices()).toEqual(prices.slice(2, 9));
    expect(fixture.nativeElement.textContent).toContain('4 Jan 2024');

    component.toggleRangeSelection();
    component.endDate.set(prices[2].date);
    component.applyCustomRange(new Event('submit'));
    expect(component.message()).toBe('Choose two different trading days.');
    expect(component.visiblePrices()).toEqual(prices.slice(2, 9));
  });

  it('anchors presets to the full source and resets when another company arrives', () => {
    component.toggleRangeSelection();
    component.startDate.set(prices[0].date);
    component.endDate.set(prices[8].date);
    component.applyCustomRange(new Event('submit'));
    component.choosePreset('1M');
    expect(component.lastPrice()?.date).toBe(prices.at(-1)?.date);
    const otherPrices = prices.map((row) => ({ ...row, adjusted_close: '50' }));
    fixture.componentRef.setInput('prices', otherPrices);
    fixture.detectChanges();
    expect(component.selectedPreset()).toBe('All');
    expect(component.visiblePrices()).toEqual(otherPrices);
  });

  it('snaps both clicks and the moving indicator to the nearest real trading day', () => {
    const step = xAt(1) - xAt(0);
    pointer('pointerdown', xAt(3) + step * 0.2);
    pointer('pointerup', xAt(3) + step * 0.2);
    expect(component.selectedPreset()).toBe('All');
    const isSelected = renderer.create.mock.calls.at(-1)?.[2] as (index: number) => boolean;
    expect(isSelected(3)).toBe(true);
    expect(isSelected(0)).toBe(false);

    // Moving between two clicks changes the end marker without moving the actual pointer.
    pointer('pointermove', xAt(4) + step * 0.4);
    expect(isSelected(3)).toBe(true);
    expect(isSelected(4)).toBe(true);
    expect(component.selectionBox()?.left).toBeCloseTo(xAt(3));
    expect(component.selectionBox()?.width).toBeCloseTo(step);
    pointer('pointermove', xAt(4) + step * 0.1);
    expect(component.selectionBox()?.width).toBeCloseTo(step);

    pointer('pointerdown', xAt(4) + step * 0.1);
    pointer('pointerup', xAt(4) + step * 0.1);
    expect(component.visiblePrices()).toEqual(prices.slice(3, 5));
    expect(component.selectedPreset()).toBe('Custom');
    const finalMarkers = renderer.create.mock.calls.at(-1)?.[2] as (index: number) => boolean;
    expect(finalMarkers(0)).toBe(true);
    expect(finalMarkers(1)).toBe(true);
    expect(finalMarkers(2)).toBe(false);
  });

  it('keeps the first point after a repeated click and allows a different second day', () => {
    for (let click = 0; click < 2; click++) {
      pointer('pointerdown', xAt(5));
      pointer('pointerup', xAt(5));
    }
    expect(component.message()).toBe('Choose two different trading days.');
    expect(component.visiblePrices()).toEqual(prices);
    pointer('pointerdown', xAt(6));
    pointer('pointerup', xAt(6));
    expect(component.visiblePrices()).toEqual(prices.slice(5, 7));
  });

  it('selects two separate touch taps after enabling Select range', () => {
    component.toggleRangeSelection();
    fixture.detectChanges();
    pointer('pointerdown', xAt(2), 'touch');
    pointer('pointerup', xAt(2), 'touch');
    expect(component.selectingRange()).toBe(true);
    pointer('pointerdown', xAt(3), 'touch');
    pointer('pointerup', xAt(3), 'touch');
    expect(component.visiblePrices()).toEqual(prices.slice(2, 4));
    expect(component.selectingRange()).toBe(false);
  });
});
