import { Component, input, output } from '@angular/core';

export type StateKind = 'loading' | 'error' | 'empty';

/**
 * The one look of a loading, error or empty state on every page (N94).
 *
 * An error always offers Try again, because every request in the app can be repeated.
 * Other actions, such as "Show all sectors", come as content marked with the `stateAction`
 * attribute and keep their own styles.
 */
@Component({
  selector: 'app-state-message',
  templateUrl: './state-message.html',
  styleUrl: './state-message.css',
  host: { '[class.framed]': 'framed()' },
})
export class StateMessage {
  readonly kind = input.required<StateKind>();
  // Without "..." for loading: the component adds the dots.
  readonly text = input.required<string>();
  // A border of its own, where the message stands alone on the page instead of in a panel.
  readonly framed = input(false);
  readonly retry = output<void>();
}
