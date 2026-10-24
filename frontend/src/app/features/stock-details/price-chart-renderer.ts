import { Injectable } from '@angular/core';
import {
  Chart,
  CategoryScale,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js';
import { Price } from '../../models/stock';
import { formatPrice, formatPriceDate } from './price-chart-data';

Chart.register(CategoryScale, LinearScale, LineController, LineElement, PointElement, Tooltip);
export type PriceLineChart = Chart<'line', number[], string>;

@Injectable({ providedIn: 'root' })
export class PriceChartRenderer {
  create(
    canvas: HTMLCanvasElement,
    prices: Price[],
    isSelectedPoint: (index: number) => boolean,
  ): PriceLineChart | null {
    const context = canvas.getContext('2d');
    if (!context) return null;
    const theme = getComputedStyle(canvas);
    const color = (role: string) => theme.getPropertyValue(`--bc-${role}`).trim();
    const days =
      prices.length > 1
        ? (Date.parse(prices[prices.length - 1].date) - Date.parse(prices[0].date)) / 86400000
        : 0;
    const axisDateFormatter = new Intl.DateTimeFormat('en-GB', {
      day: days <= 92 ? 'numeric' : undefined,
      month: days <= 1830 ? 'short' : undefined,
      year: days > 92 ? 'numeric' : undefined,
      timeZone: 'UTC',
    });
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
            pointRadius: (context) => (isSelectedPoint(context.dataIndex) ? 5 : 0),
            pointHoverRadius: (context) => (isSelectedPoint(context.dataIndex) ? 5 : 0),
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
          if (ticks) ticks.maxTicksLimit = size.width < 500 ? 4 : 8;
        },
        plugins: {
          tooltip: {
            backgroundColor: color('surface'),
            titleColor: color('text'),
            bodyColor: color('text'),
            borderColor: color('border'),
            borderWidth: 1,
            callbacks: {
              title: (items) =>
                items.length ? formatPriceDate(prices[items[0].dataIndex].date) : '',
              label: (item) =>
                item.parsed.y === null ? 'Not available' : formatPrice(item.parsed.y),
            },
          },
        },
        scales: {
          x: {
            ticks: {
              maxTicksLimit: canvas.clientWidth < 500 ? 4 : 8,
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
