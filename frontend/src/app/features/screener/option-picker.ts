import { Component, ElementRef, inject, input, output, signal } from '@angular/core';

export interface PickerOption {
  key: string;
  label: string;
  // Shown after the label, e.g. the number of companies in a sector.
  count?: number;
}

/** One ticked or cleared checkbox. */
export interface OptionToggle {
  key: string;
  chosen: boolean;
}

/**
 * A button that opens a panel of checkboxes: it picks sectors, or the columns of the table.
 * It owns only its open state. It reports each single change, and the page, which keeps the
 * selection, applies it to its current state; so two quick clicks can never undo each other.
 */
@Component({
  selector: 'app-option-picker',
  templateUrl: './option-picker.html',
  styleUrl: './option-picker.css',
  host: {
    '[class.container-anchored]': 'anchorToContainer()',
    '(document:pointerdown)': 'onDocumentPointerDown($event)',
  },
})
export class OptionPicker {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  // What the control is about, read by assistive technology before the summary ("Sector: ...").
  readonly name = input.required<string>();
  readonly summary = input.required<string>();
  readonly options = input.required<PickerOption[]>();
  readonly selected = input.required<string[]>();
  // The "exclude" row appears only when a text is given; sectors use it, columns do not.
  readonly excludeLabel = input<string | null>(null);
  readonly exclude = input(false);
  // A control that stands at the left of a toolbar opens its panel from the left edge of that
  // toolbar. One at the right of a field opens leftwards from its own edge. Either way the panel
  // stays inside the page.
  readonly anchorToContainer = input(false);
  readonly resetLabel = input.required<string>();
  readonly resetDisabled = input.required<boolean>();

  readonly toggled = output<OptionToggle>();
  readonly excludeChange = output<boolean>();
  readonly reset = output<void>();

  readonly open = signal(false);

  isChosen(key: string): boolean {
    return this.selected().includes(key);
  }

  onToggle(open: boolean): void {
    this.open.set(open);
  }

  /** The panel lies over the page, so a press anywhere else closes it, as a menu would. */
  onDocumentPointerDown(event: Event): void {
    if (!this.open()) {
      return;
    }
    const target = event.target;
    if (target instanceof Node && this.host.nativeElement.contains(target)) {
      return;
    }
    this.open.set(false);
  }

  closeAndFocus(summary: HTMLElement): void {
    this.open.set(false);
    summary.focus();
  }
}
