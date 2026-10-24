import {
  Component,
  computed,
  effect,
  ElementRef,
  HostListener,
  inject,
  input,
  OnChanges,
  OnDestroy,
  signal,
  viewChild,
} from '@angular/core';
import { Price } from '../../models/stock';
import { ThemeService } from '../../services/theme.service';
import {
  formatPeriodChange,
  formatPrice,
  formatPriceDate,
  periodChange,
  PricePreset,
  PriceRange,
  pricesForPreset,
  pricesInRange,
} from './price-chart-data';
import { PriceChartRenderer, PriceLineChart } from './price-chart-renderer';

interface PressedPoint {
  pointerId: number;
  index: number;
  continuingSelection: boolean;
}
interface SelectionBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

@Component({
  selector: 'app-price-chart',
  templateUrl: './price-chart.html',
  styleUrl: './price-chart.css',
})
export class PriceChart implements OnChanges, OnDestroy {
  private readonly themeService = inject(ThemeService);
  private readonly renderer = inject(PriceChartRenderer);
  private readonly rangeToggle = viewChild<ElementRef<HTMLButtonElement>>('rangeToggle');
  private readonly canvas = viewChild<ElementRef<HTMLCanvasElement>>('canvas');
  private readonly range = signal<PriceRange | null>(null);
  private chart: PriceLineChart | null = null;
  private pressedPoint: PressedPoint | null = null;
  private anchorIndex: number | null = null;
  private endIndex: number | null = null;

  readonly prices = input.required<Price[]>();
  readonly presets: PricePreset[] = ['1M', '6M', '1Y', '5Y', 'All'];
  readonly selectedPreset = signal<PricePreset | 'Custom'>('All');
  readonly selectingRange = signal(false);
  readonly startDate = signal('');
  readonly endDate = signal('');
  readonly message = signal('');
  readonly selectionBox = signal<SelectionBox | null>(null);
  readonly visiblePrices = computed(() => pricesInRange(this.prices(), this.range()));
  readonly firstPrice = computed(() => this.visiblePrices()[0]);
  readonly lastPrice = computed(() => this.visiblePrices().at(-1));
  readonly change = computed(() => periodChange(this.visiblePrices()));
  readonly changeLabel = computed(() => formatPeriodChange(this.change()));
  readonly finalPriceLabel = computed(() => {
    const last = this.lastPrice();
    return last ? formatPrice(Number(last.adjusted_close)) : 'Not available';
  });
  readonly formatDate = formatPriceDate;

  constructor() {
    effect(() => {
      this.themeService.mode();
      const prices = this.visiblePrices();
      const canvas = this.canvas()?.nativeElement;
      if (!canvas) return;
      this.clearPointSelection(false);
      this.chart?.destroy();
      this.chart = this.renderer.create(canvas, prices, (index) => this.isSelectedPoint(index));
    });
  }

  ngOnChanges(): void {
    this.cancelSelection();
    this.selectedPreset.set('All');
    this.range.set(null);
  }

  choosePreset(preset: PricePreset): void {
    this.cancelSelection();
    this.setRange(pricesForPreset(this.prices(), preset), preset);
  }

  toggleRangeSelection(): void {
    if (this.selectingRange()) {
      this.cancelSelection();
      return;
    }
    this.startDate.set(this.firstPrice()?.date ?? '');
    this.endDate.set(this.lastPrice()?.date ?? '');
    this.message.set('');
    this.selectingRange.set(true);
  }

  applyCustomRange(event: Event): void {
    event.preventDefault();
    const start = this.startDate();
    const end = this.endDate();
    if (!start || !end || start > end) {
      this.message.set('Choose a start date on or before the end date.');
      return;
    }
    this.commitCustomRange(pricesInRange(this.prices(), { start, end }));
  }

  onPointerDown(event: PointerEvent): void {
    if (!event.isPrimary) {
      this.cancelSelection();
      return;
    }
    if (event.button !== 0) return;
    if (event.pointerType === 'touch' && !this.selectingRange()) return;
    const point = this.pointerPosition(event);
    const area = this.chart?.chartArea;
    if (
      !point ||
      !area ||
      point.x < area.left ||
      point.x > area.right ||
      point.y < area.top ||
      point.y > area.bottom
    )
      return;
    this.message.set('');
    const index = this.indexAt(point.x);
    this.pressedPoint = {
      pointerId: event.pointerId,
      index,
      continuingSelection: this.anchorIndex !== null,
    };
    if (this.anchorIndex === null) this.anchorIndex = index;
    this.endIndex = index;
    this.refreshPointSelection();
    this.canvas()?.nativeElement.setPointerCapture(event.pointerId);
  }

  onPointerMove(event: PointerEvent): void {
    const point = this.pointerPosition(event);
    if (this.anchorIndex === null || !point) return;
    if (this.pressedPoint && this.pressedPoint.pointerId !== event.pointerId) return;
    const index = this.indexAt(point.x);
    if (index === this.endIndex) return;
    this.endIndex = index;
    this.refreshPointSelection();
  }

  onPointerUp(event: PointerEvent): void {
    const pressed = this.pressedPoint;
    const point = this.pointerPosition(event);
    if (!pressed || pressed.pointerId !== event.pointerId || !point || this.anchorIndex === null)
      return;
    this.endIndex = this.indexAt(point.x);
    this.releasePointer();
    if (pressed.continuingSelection || this.endIndex !== pressed.index) {
      const first = Math.min(this.anchorIndex, this.endIndex);
      const last = Math.max(this.anchorIndex, this.endIndex);
      this.commitCustomRange(this.visiblePrices().slice(first, last + 1));
    } else {
      // A first click pins the start. Moving without holding tracks the end day.
      this.refreshPointSelection();
    }
  }

  onPointerCancelled(event: PointerEvent): void {
    if (this.pressedPoint?.pointerId === event.pointerId) this.cancelSelection();
  }

  @HostListener('document:keydown.escape')
  cancelSelection(): void {
    const restoreFocus = this.selectingRange();
    this.clearPointSelection();
    this.selectingRange.set(false);
    this.message.set('');
    if (restoreFocus) this.rangeToggle()?.nativeElement.focus({ preventScroll: true });
  }

  private commitCustomRange(prices: Price[]): void {
    if (prices.length < 2) {
      this.message.set('Choose two different trading days.');
      return;
    }
    this.cancelSelection();
    this.setRange(prices, 'Custom');
  }

  private setRange(prices: Price[], preset: PricePreset | 'Custom'): void {
    this.range.set(
      prices.length
        ? {
            start: prices[0].date,
            end: prices[prices.length - 1].date,
          }
        : null,
    );
    this.selectedPreset.set(preset);
  }

  private pointerPosition(event: PointerEvent): { x: number; y: number } | null {
    const canvas = this.canvas()?.nativeElement;
    if (!canvas || !this.chart) return null;
    const bounds = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - bounds.left) * this.chart.width) / bounds.width,
      y: ((event.clientY - bounds.top) * this.chart.height) / bounds.height,
    };
  }

  private indexAt(x: number): number {
    const index = this.chart?.scales['x'].getValueForPixel(x) ?? 0;
    return Math.max(0, Math.min(this.visiblePrices().length - 1, Math.round(index)));
  }

  private releasePointer(): void {
    const pointerId = this.pressedPoint?.pointerId;
    this.pressedPoint = null;
    const canvas = this.canvas()?.nativeElement;
    if (pointerId !== undefined && canvas?.hasPointerCapture(pointerId)) {
      canvas.releasePointerCapture(pointerId);
    }
  }

  private clearPointSelection(redraw = true): void {
    this.releasePointer();
    this.anchorIndex = null;
    this.endIndex = null;
    this.selectionBox.set(null);
    if (redraw) {
      this.setTooltipEnabled(true);
      this.chart?.update('none');
    }
  }

  private isSelectedPoint(index: number): boolean {
    if (this.anchorIndex !== null) return index === this.anchorIndex || index === this.endIndex;
    return (
      this.selectedPreset() === 'Custom' &&
      (index === 0 || index === this.visiblePrices().length - 1)
    );
  }

  private refreshPointSelection(): void {
    const chart = this.chart;
    if (!chart || this.anchorIndex === null || this.endIndex === null) return;
    this.setTooltipEnabled(false);
    chart.update('none');
    // Both edges use the actual session positions, never the raw mouse coordinates.
    const start = chart.scales['x'].getPixelForValue(this.anchorIndex);
    const end = chart.scales['x'].getPixelForValue(this.endIndex);
    this.selectionBox.set({
      left: Math.min(start, end),
      top: chart.chartArea.top,
      width: Math.abs(end - start),
      height: chart.chartArea.bottom - chart.chartArea.top,
    });
  }

  private setTooltipEnabled(enabled: boolean): void {
    const tooltip = this.chart?.options.plugins?.tooltip;
    if (!tooltip || tooltip.enabled === enabled) return;
    tooltip.enabled = enabled;
  }

  ngOnDestroy(): void {
    this.clearPointSelection(false);
    this.chart?.destroy();
  }
}
