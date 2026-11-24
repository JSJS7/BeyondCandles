import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { SlideDetails, slideOpen, slideShut } from './slide';

const SUMMARY_HEIGHT = 40;
const OPEN_HEIGHT = 400;

@Component({
  imports: [SlideDetails],
  template: `<details appSlideDetails>
    <summary>Guide</summary>
    <p>Text</p>
  </details>`,
})
class GuideHost {}

describe('slide', () => {
  afterEach(() => vi.unstubAllGlobals());

  // Without the Web Animations API (as here, and where motion is reduced) the change must still
  // happen at once: Angular removes an element only after slideOut reports that it is done.
  it('finishes at once when the browser cannot animate', () => {
    const element = document.createElement('div');
    document.body.append(element);
    const done: string[] = [];

    slideOpen(element, () => done.push('open'));
    slideShut(element, () => done.push('shut'));

    expect(done).toEqual(['open', 'shut']);
    expect(element.style.overflow).toBe('');
    element.remove();
  });

  describe('SlideDetails', () => {
    // The browser reports the animated height while a slide runs and the natural height once it
    // stops; heightDuringSlide stands for the first, and stopping a slide clears it.
    let heightDuringSlide: number | null;
    let slideStarts: string[];
    let details: HTMLDetailsElement;
    let summary: HTMLElement;

    beforeEach(() => {
      vi.stubGlobal(
        'matchMedia',
        vi.fn(() => ({ matches: false })),
      );
      const fixture = TestBed.createComponent(GuideHost);
      fixture.detectChanges();
      details = fixture.nativeElement.querySelector('details');
      summary = fixture.nativeElement.querySelector('summary');
      heightDuringSlide = null;
      slideStarts = [];

      Object.defineProperty(summary, 'offsetHeight', { get: () => SUMMARY_HEIGHT });
      Object.defineProperty(details, 'offsetHeight', {
        get: () => heightDuringSlide ?? (details.open ? OPEN_HEIGHT : SUMMARY_HEIGHT),
      });
      details.animate = vi.fn((keyframes: Keyframe[]) => {
        slideStarts.push(String(keyframes[0]['height']));
        const animation = {
          addEventListener: vi.fn(),
          cancel: () => {
            heightDuringSlide = null;
          },
        };
        return animation as unknown as Animation;
      });
    });

    // A second click while the guide opens closes it from the height reached; measured after the
    // slide stopped, it first jumped to the full height and only then closed.
    it('closes from the height reached when clicked again while opening', () => {
      summary.click();
      heightDuringSlide = 150;

      summary.click();

      expect(slideStarts).toEqual([`${SUMMARY_HEIGHT}px`, '150px']);
    });

    // A second click while the guide closes opens it again from the height reached; it used to
    // jump to the full height at once, without a slide.
    it('opens again from the height reached when clicked again while closing', () => {
      details.open = true;
      summary.click();
      heightDuringSlide = 250;

      summary.click();

      expect(slideStarts).toEqual([`${OPEN_HEIGHT}px`, '250px']);
      expect(details.open).toBe(true);
    });
  });
});
