import { afterNextRender, DestroyRef, Directive, ElementRef, inject, signal } from '@angular/core';

/**
 * Tells whether the table inside a scroll area is wider than the area, so that a hint above it
 * can say that the table scrolls sideways. The width depends on the data (a label such as
 * "Discontinued ops" widens its column) and on the window, so the table is measured rather than
 * compared with a fixed screen width.
 *
 * Use: `<div class="table-scroll" appScrollOverflow #scroll="scrollOverflow">` and
 * `@if (scroll.overflows()) { hint }`.
 */
@Directive({
  selector: '[appScrollOverflow]',
  exportAs: 'scrollOverflow',
})
export class ScrollOverflow {
  readonly overflows = signal(false);

  constructor() {
    const scroller: HTMLElement = inject(ElementRef).nativeElement;
    const destroyRef = inject(DestroyRef);

    afterNextRender(() => {
      // Test environments without layout have no ResizeObserver; the hint then stays hidden.
      if (typeof ResizeObserver === 'undefined') {
        return;
      }
      const measure = () => {
        this.overflows.set(scroller.scrollWidth > scroller.clientWidth);
      };
      const observer = new ResizeObserver(measure);
      observer.observe(scroller);
      // The table changes size with new data or wrapped labels, its area with the window.
      const table = scroller.querySelector('table');
      if (table) {
        observer.observe(table);
      }
      destroyRef.onDestroy(() => observer.disconnect());
    });
  }
}
