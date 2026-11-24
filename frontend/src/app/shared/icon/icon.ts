import { Component, input } from '@angular/core';

export type IconName = 'search' | 'filter' | 'history' | 'sun' | 'moon' | 'close';

/**
 * A drawn icon in the colour of the text around it, as big as its font size.
 *
 * It replaces text symbols such as ⌕, ☀ and ×: a symbol comes from whatever font the device has,
 * so its size, its place in the line and even its colour (Android shows ☀ as a colour emoji)
 * change from device to device. The icon is decoration; the control beside it carries the name.
 */
@Component({
  selector: 'app-icon',
  templateUrl: './icon.html',
  styleUrl: './icon.css',
})
export class Icon {
  readonly name = input.required<IconName>();
}
