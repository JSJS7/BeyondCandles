import { afterNextRender, DestroyRef, Directive, ElementRef, inject, input } from '@angular/core';
import { ScrollMemory } from './scroll-memory';

/**
 * Marks an area with its own scrolling, so that Back returns to the same place inside it, as it
 * does for the page (scroll-memory.ts). The key names the area on its page.
 */
@Directive({ selector: '[appScrollMemory]' })
export class ScrollMemoryArea {
  readonly key = input.required<string>({ alias: 'appScrollMemory' });

  constructor() {
    const element: HTMLElement = inject(ElementRef).nativeElement;
    const scrollMemory = inject(ScrollMemory);

    // After the first render the area holds its content, so a restored place can be reached.
    afterNextRender(() => {
      scrollMemory.registerArea(this.key(), element);
    });
    inject(DestroyRef).onDestroy(() => {
      scrollMemory.unregisterArea(this.key(), element);
    });
  }
}
