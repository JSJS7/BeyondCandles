// The text of the browser tab: what the page shows first, then the site name (WCAG 2.4.2).
const SITE_NAME = 'BeyondCandles';

export function pageTitle(pageName: string): string {
  return `${pageName} · ${SITE_NAME}`;
}
