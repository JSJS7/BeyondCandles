import { afterNextRender, Component, inject, Injector, input, output } from '@angular/core';
import { ColumnKey, ScreenerColumn } from './screener-columns';

/** What one criterion row shows; the page computes it from its state and the loaded rows. */
export interface CriterionView {
  key: ColumnKey;
  label: string;
  unit: string;
  min: string;
  max: string;
  minInvalid: boolean;
  maxInvalid: boolean;
  reversed: boolean;
  // Companies left out because they have no value; zero while the criterion limits nothing.
  leftOut: number;
  stats: { count: number; median: string; p10: string; p90: string } | null;
}

export interface BoundChange {
  key: ColumnKey;
  bound: 'min' | 'max';
  text: string;
}

/**
 * The list of ratio ranges with the control that adds one. It holds no state of its own:
 * every change goes to the page, which filters the rows and keeps the address.
 */
@Component({
  selector: 'app-criteria-editor',
  templateUrl: './criteria-editor.html',
  styleUrl: './criteria-editor.css',
})
export class CriteriaEditor {
  private readonly injector = inject(Injector);

  readonly views = input.required<CriterionView[]>();
  readonly available = input.required<ScreenerColumn[]>();
  readonly added = output<ColumnKey>();
  readonly boundChanged = output<BoundChange>();
  readonly removed = output<ColumnKey>();
  readonly cleared = output<void>();

  // W31: in some browsers (Chrome on Windows) an arrow key changes a closed list at once and
  // reports it as a change. Adding there would move the focus away while the reader is still
  // browsing (WCAG 3.2.2), so a choice made with the keyboard waits for Enter. A choice made
  // with a mouse or a finger in the open list is final and adds at once.
  private choosingWithKeyboard = false;

  onSelectPointerDown(): void {
    this.choosingWithKeyboard = false;
  }

  onSelectKeydown(event: KeyboardEvent, select: HTMLSelectElement): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.addChosen(select);
      return;
    }
    this.choosingWithKeyboard = true;
  }

  onSelectChange(select: HTMLSelectElement): void {
    if (this.choosingWithKeyboard) {
      return;
    }
    this.addChosen(select);
  }

  /** A ratio browsed to but not confirmed is not added; the list returns to its prompt. */
  onSelectBlur(select: HTMLSelectElement): void {
    select.value = '';
    this.choosingWithKeyboard = false;
  }

  private addChosen(select: HTMLSelectElement): void {
    const key = select.value;
    select.value = '';
    this.add(key);
  }

  add(key: string): void {
    const column = this.available().find((candidate) => candidate.key === key);
    if (!column) {
      return;
    }

    this.added.emit(column.key);
    // The next thing to do is typing a limit, so the focus goes to the new minimum field.
    afterNextRender(() => document.getElementById(`criterion-min-${column.key}`)?.focus(), {
      injector: this.injector,
    });
  }

  changeBound(key: ColumnKey, bound: 'min' | 'max', text: string): void {
    this.boundChanged.emit({ key, bound, text });
  }
}
