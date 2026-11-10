import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { StateKind, StateMessage } from './state-message';

@Component({
  imports: [StateMessage],
  template: `
    <app-state-message [kind]="kind()" [text]="text()" (retry)="retries = retries + 1">
      @if (withAction()) {
        <button stateAction class="extra" type="button">Clear search</button>
      }
    </app-state-message>
  `,
})
class StateHost {
  readonly kind = signal<StateKind>('loading');
  readonly text = signal('Loading companies');
  readonly withAction = signal(false);
  retries = 0;
}

describe('State message', () => {
  function render(kind: StateKind, text: string, withAction = false) {
    const fixture = TestBed.createComponent(StateHost);
    fixture.componentInstance.kind.set(kind);
    fixture.componentInstance.text.set(text);
    fixture.componentInstance.withAction.set(withAction);
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    return { fixture, element };
  }

  it('announces loading politely, with dots hidden from screen readers and no action', () => {
    const { element } = render('loading', 'Loading companies');

    const text = element.querySelector('.state-text');
    expect(text?.getAttribute('role')).toBe('status');
    expect(text?.textContent).toContain('Loading companies...');
    expect(element.querySelector('.loading-dots')?.getAttribute('aria-hidden')).toBe('true');
    expect(element.querySelector('button')).toBeNull();
    // An empty action row is hidden by CSS, so it adds no gap below the text.
    expect(element.querySelector('.state-actions')?.matches(':empty')).toBe(true);
  });

  it('interrupts with an error and offers Try again, which reports the retry', () => {
    const { fixture, element } = render('error', 'Failed to load stock details.');

    expect(element.querySelector('.state-text')?.getAttribute('role')).toBe('alert');
    expect(element.querySelector<HTMLElement>('.loading-dots')?.hidden).toBe(true);
    const retry = element.querySelector<HTMLButtonElement>('.retry-button');
    expect(retry?.textContent?.trim()).toBe('Try again');
    retry?.click();
    expect(fixture.componentInstance.retries).toBe(1);
  });

  it('shows an empty result with the action given by the page, without Try again', () => {
    const { element } = render('empty', 'No company matches "zzz".', true);

    expect(element.querySelector('.state-text')?.getAttribute('role')).toBe('status');
    expect(element.querySelector('.state-text')?.textContent).not.toContain('Clear search');
    expect(element.querySelector('.state-actions .extra')?.textContent).toBe('Clear search');
    expect(element.querySelector('.retry-button')).toBeNull();
  });
});
