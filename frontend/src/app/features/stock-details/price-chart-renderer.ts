import { Injectable } from '@angular/core';
import {
  CategoryScale,
  Chart,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js';
import { Price } from '../../models/stock';
import { formatDate } from '../../shared/date-format';
import { formatPrice, yearlyTickIndexes } from './price-chart-data';

Chart.register(CategoryScale, LinearScale, LineController, LineElement, PointElement, Tooltip);
export type PriceLineChart = Chart<'line', number[], string>;

const MILLISECONDS_PER_DAY = 86_400_000;
// Up to about three months the axis names days; up to about five years, months; beyond, years.
const DAY_LABELS_UP_TO_DAYS = 92;
const MONTH_LABELS_UP_TO_DAYS = 1830;
// A narrower chart, such as on a phone, has room for fewer axis labels.
const NARROW_CHART_WIDTH = 500;
const NARROW_CHART_TICKS = 4;
const WIDE_CHART_TICKS = 8;

function tickLimit(chartWidth: number): number {
  return chartWidth < NARROW_CHART_WIDTH ? NARROW_CHART_TICKS : WIDE_CHART_TICKS;
}

function daysCovered(prices: Price[]): number {
  if (prices.length < 2) {
    return 0;
  }
  const first = Date.parse(prices[0].date);
  const last = Date.parse(prices[prices.length - 1].date);
  return (last - first) / MILLISECONDS_PER_DAY;
}

@Injectable({ providedIn: 'root' })
export class PriceChartRenderer {
  create(
    canvas: HTMLCanvasElement,
    prices: Price[],
    isSelectedPoint: (index: number) => boolean,
  ): PriceLineChart | null {
    const context = canvas.getContext('2d');
    if (!context) {
      return null;
    }

    // The chart reads the palette of the current theme, so it is drawn again when it changes.
    const theme = getComputedStyle(canvas);
    const color = (role: string) => theme.getPropertyValue(`--bc-${role}`).trim();

    const days = daysCovered(prices);
    const showsDays = days <= DAY_LABELS_UP_TO_DAYS;
    const showsMonths = days <= MONTH_LABELS_UP_TO_DAYS;
    const axisDateFormatter = new Intl.DateTimeFormat('en-GB', {
      day: showsDays ? 'numeric' : undefined,
      month: showsMonths ? 'short' : undefined,
      year: showsDays ? undefined : 'numeric',
      timeZone: 'UTC',
    });
    // Years only on the axis: automatic skipping would repeat or drop years unevenly.
    const labelsYears = !showsMonths;

    return new Chart(context, {
      type: 'line',
      data: {
        labels: prices.map((price) => price.date),
        datasets: [
          {
            label: 'Adjusted close (USD)',
            data: prices.map((price) => Number(price.adjusted_close)),
            borderColor: color('primary'),
            borderWidth: 2,
            pointRadius: (point) => (isSelectedPoint(point.dataIndex) ? 5 : 0),
            pointHoverRadius: (point) => (isSelectedPoint(point.dataIndex) ? 5 : 0),
            pointBackgroundColor: color('primary'),
            pointBorderColor: color('surface'),
            pointBorderWidth: 2,
            pointHitRadius: 10,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { intersect: false, mode: 'index' },
        onResize(chart, size) {
          const ticks = chart.options.scales?.['x']?.ticks;
          if (ticks) {
            ticks.maxTicksLimit = tickLimit(size.width);
          }
        },
        plugins: {
          tooltip: {
            backgroundColor: color('surface'),
            titleColor: color('text'),
            bodyColor: color('text'),
            borderColor: color('border'),
            borderWidth: 1,
            callbacks: {
              title: (items) => (items.length ? formatDate(prices[items[0].dataIndex].date) : ''),
              label: (item) =>
                item.parsed.y === null ? 'Not available' : formatPrice(item.parsed.y),
            },
          },
        },
        scales: {
          x: {
            // Read at every layout: the canvas has its real width only after the first one.
            afterBuildTicks: (axis) => {
              if (!labelsYears) {
                return;
              }
              const indexes = yearlyTickIndexes(prices, tickLimit(axis.chart.width));
              if (indexes) {
                axis.ticks = indexes.map((value) => ({ value }));
              }
            },
            ticks: {
              autoSkip: !labelsYears,
              maxTicksLimit: tickLimit(canvas.clientWidth),
              maxRotation: 0,
              color: color('muted'),
              callback: (value) => {
                const date = prices[Number(value)]?.date;
                return date ? axisDateFormatter.format(new Date(`${date}T00:00:00Z`)) : '';
              },
            },
            grid: { color: color('border') },
          },
          y: {
            ticks: { color: color('muted'), callback: (value) => formatPrice(Number(value)) },
            grid: { color: color('border') },
          },
        },
      },
    });
  }
}
