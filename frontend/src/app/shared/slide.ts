// A short slide down when something opens on the page and a slide up when it closes, instead of
// appearing or disappearing at once. The Web Animations API animates the height in every browser;
// CSS cannot animate to an automatic height in all of them yet.
import { AnimationCallbackEvent, Directive, ElementRef, inject, input } from '@angular/core';

const SLIDE_DURATION_MS = 200;
const SLIDE_EASING = 'ease-out';

// The vertical sizes that change together, so that nothing jumps at the start or the end.
const VERTICAL_PROPERTIES = [
  'height',
  'paddingTop',
  'paddingBottom',
  'marginTop',
  'marginBottom',
] as const;

type VerticalSizes = Record<(typeof VERTICAL_PROPERTIES)[number], string>;

// Animations are off when the reader asks for less motion, and where the browser has no
// Web Animations API (as in unit tests).
function canAnimate(element: HTMLElement): boolean {
  if (typeof element.animate !== 'function' || typeof window.matchMedia !== 'function') {
    return false;
  }
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  return !reducedMotion;
}

function openSizes(element: HTMLElement): VerticalSizes {
  const style = getComputedStyle(element);
  return {
    // Every element uses border-box sizing, so the height includes padding and border.
    height: `${element.offsetHeight}px`,
    paddingTop: style.paddingTop,
    paddingBottom: style.paddingBottom,
    marginTop: style.marginTop,
    marginBottom: style.marginBottom,
  };
}

function closedSizes(height: number): VerticalSizes {
  return {
    height: `${height}px`,
    paddingTop: '0px',
    paddingBottom: '0px',
    marginTop: '0px',
    marginBottom: '0px',
  };
}

/** Animates the element between two sets of sizes, clipping what does not fit yet. */
function slide(element: HTMLElement, from: Keyframe, to: Keyframe): Animation {
  element.style.overflow = 'hidden';
  const animation = element.animate([from, to], {
    duration: SLIDE_DURATION_MS,
    easing: SLIDE_EASING,
  });
  const restore = (): void => {
    element.style.overflow = '';
  };
  animation.addEventListener('finish', restore);
  animation.addEventListener('cancel', restore);
  return animation;
}

/** Slides an element open from nothing; it must already be in the page at its full size. */
export function slideOpen(element: HTMLElement, onDone?: () => void): void {
  if (!canAnimate(element)) {
    onDone?.();
    return;
  }
  const animation = slide(element, closedSizes(0), openSizes(element));
  if (onDone) {
    animation.addEventListener('finish', onDone);
  }
}

/** Slides an element shut to nothing and then calls onDone, which removes or hides it. */
export function slideShut(element: HTMLElement, onDone: () => void): void {
  if (!canAnimate(element)) {
    onDone();
    return;
  }
  const animation = slide(element, openSizes(element), closedSizes(0));
  animation.addEventListener('finish', onDone);
}

/** For `(animate.enter)` on an element that an `@if` adds. */
export function slideIn(event: AnimationCallbackEvent): void {
  slideOpen(event.target as HTMLElement, () => event.animationComplete());
}

/** For `(animate.leave)` on an element that an `@if` removes. */
export function slideOut(event: AnimationCallbackEvent): void {
  slideShut(event.target as HTMLElement, () => event.animationComplete());
}

// A drawer at the left edge of the screen comes in from that edge and goes back out through it.
const OFF_SCREEN_LEFT: Keyframe = { transform: 'translateX(-100%)' };
const ON_SCREEN: Keyframe = { transform: 'translateX(0)' };

/** Slides a drawer in from the left edge; it must already be shown. */
export function slideInFromLeft(element: HTMLElement): void {
  if (!canAnimate(element)) {
    return;
  }
  element.animate([OFF_SCREEN_LEFT, ON_SCREEN], {
    duration: SLIDE_DURATION_MS,
    easing: SLIDE_EASING,
  });
}

/** Slides a drawer out through the left edge and then calls onDone, which hides it. */
export function slideOutToLeft(element: HTMLElement, onDone: () => void): void {
  if (!canAnimate(element)) {
    onDone();
    return;
  }
  // Kept off screen after the end, so that it does not flash back before onDone hides it.
  const animation = element.animate([ON_SCREEN, OFF_SCREEN_LEFT], {
    duration: SLIDE_DURATION_MS,
    easing: SLIDE_EASING,
    fill: 'forwards',
  });
  animation.addEventListener('finish', () => {
    onDone();
    animation.cancel();
  });
}

/**
 * Slides a `<details>` element open and shut. Without a panel, the details element itself grows
 * from its summary to its full height. With a panel (a drop-down that lies over the page), the
 * panel slides instead; it only opens with a slide, because it also closes from elsewhere.
 */
@Directive({
  selector: 'details[appSlideDetails]',
  host: { '(click)': 'onClick($event)' },
})
export class SlideDetails {
  readonly slidePanel = input<HTMLElement | null>(null);

  private readonly details: HTMLDetailsElement = inject(ElementRef).nativeElement;
  private running: Animation | null = null;
  private closing = false;

  onClick(event: MouseEvent): void {
    const summary = this.details.querySelector(':scope > summary');
    const target = event.target;
    if (!(target instanceof Element) || target.closest('summary') !== summary) {
      return;
    }
    if (!canAnimate(this.details)) {
      return;
    }
    // The animation decides when the details element opens or closes.
    event.preventDefault();
    // Measured before a running slide stops, so that a second click turns back from the height
    // reached instead of jumping to the full height first.
    const currentHeight = this.details.offsetHeight;
    const wasClosing = this.closing;
    this.running?.cancel();
    this.running = null;
    this.closing = false;
    // While it closes it is still open; a second click then opens it again.
    if (this.details.open && !wasClosing) {
      this.close(currentHeight);
    } else {
      this.open(currentHeight);
    }
  }

  private open(fromHeight: number): void {
    const panel = this.slidePanel();
    if (panel) {
      this.details.open = true;
      slideOpen(panel);
      return;
    }
    this.details.open = true;
    const openHeight = this.details.offsetHeight;
    this.running = slide(
      this.details,
      { height: `${fromHeight}px` },
      { height: `${openHeight}px` },
    );
  }

  private close(fromHeight: number): void {
    const panel = this.slidePanel();
    if (panel) {
      this.details.open = false;
      return;
    }
    const summary = this.details.querySelector<HTMLElement>(':scope > summary');
    const closedHeight = summary?.offsetHeight ?? 0;
    const animation = slide(
      this.details,
      { height: `${fromHeight}px` },
      { height: `${closedHeight}px` },
    );
    animation.addEventListener('finish', () => {
      this.closing = false;
      this.running = null;
      this.details.open = false;
    });
    this.closing = true;
    this.running = animation;
  }
}
