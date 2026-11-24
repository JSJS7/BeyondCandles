import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Layout } from './layout';

describe('Layout', () => {
  let component: Layout;
  let fixture: ComponentFixture<Layout>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Layout],
      providers: [
        provideRouter([
          { path: 'screener', children: [] },
          { path: 'other', children: [] },
        ]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Layout);
    component = fixture.componentInstance;
    fixture.detectChanges();
    TestBed.inject(HttpTestingController).expectOne('http://127.0.0.1:8000/stocks').flush([]);
    await fixture.whenStable();
  });

  afterEach(() => {
    TestBed.inject(HttpTestingController).verify();
    vi.unstubAllGlobals();
  });

  it('renders the layout with a theme switch independently of a running backend', () => {
    expect(component).toBeTruthy();
    expect(fixture.nativeElement.querySelector('button[aria-label*="mode"]')).not.toBeNull();
  });

  function mockDialog(): HTMLDialogElement {
    const dialog: HTMLDialogElement = fixture.nativeElement.querySelector('dialog');
    // Native dialog focus trapping is checked in Chrome; here we control its lifecycle.
    dialog.showModal = vi.fn(() => {
      dialog.setAttribute('open', '');
      dialog.querySelector<HTMLButtonElement>('.menu-close')?.focus();
    });
    dialog.close = vi.fn(() => {
      dialog.removeAttribute('open');
      dialog.dispatchEvent(new Event('close'));
    });
    return dialog;
  }

  it('opens the mobile menu and restores focus to the toolbar button when closed', () => {
    const dialog = mockDialog();
    const opener: HTMLButtonElement = fixture.nativeElement.querySelector('.menu-button');
    opener.click();
    fixture.detectChanges();
    expect(dialog.showModal).toHaveBeenCalledOnce();
    expect(opener.getAttribute('aria-expanded')).toBe('true');
    fixture.nativeElement.querySelector('.menu-close').click();
    fixture.detectChanges();
    expect(dialog.open).toBe(false);
    expect(component.menuOpen()).toBe(false);
    expect(document.activeElement).toBe(opener);
  });

  it('keeps inside clicks open and closes the menu through the backdrop or Escape', () => {
    const dialog = mockDialog();
    vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      right: 280,
      bottom: 844,
      width: 280,
      height: 844,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    const opener: HTMLButtonElement = fixture.nativeElement.querySelector('.menu-button');
    opener.click();
    dialog.querySelector('.brand')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(component.menuOpen()).toBe(true);
    dialog.dispatchEvent(new MouseEvent('click', { clientX: 350, clientY: 100 }));
    expect(component.menuOpen()).toBe(false);
    opener.click();
    dialog.dispatchEvent(new Event('cancel'));
    expect(component.menuOpen()).toBe(false);
  });

  it('closes the mobile menu after a navigation link inside it is used', () => {
    // The menu covers the page, so the user would not see the new page behind it.
    const dialog = mockDialog();
    fixture.nativeElement.querySelector('.menu-button').click();
    expect(component.menuOpen()).toBe(true);

    dialog.querySelector<HTMLAnchorElement>('a[href="/screener"]')?.click();

    expect(dialog.open).toBe(false);
    expect(component.menuOpen()).toBe(false);
  });

  it('closes an open mobile menu when the viewport changes to desktop width', () => {
    const dialog = mockDialog();
    const opener: HTMLButtonElement = fixture.nativeElement.querySelector('.menu-button');
    opener.click();
    component.onViewportResize();
    expect(dialog.open).toBe(false);
    expect(component.menuOpen()).toBe(false);
  });

  it('keeps the mobile menu open while the phone layout applies, as on a phone turned sideways', () => {
    // A phone turned sideways (844 x 390) is wide but low and keeps the phone layout, so turning
    // it must not close the menu. The browser decides the layout with the style sheets' query.
    const dialog = mockDialog();
    const matchMedia = vi.fn(() => ({ matches: true }));
    vi.stubGlobal('matchMedia', matchMedia);
    fixture.nativeElement.querySelector('.menu-button').click();

    component.onViewportResize();

    expect(matchMedia).toHaveBeenCalledWith(
      '(max-width: 767px), (max-height: 500px) and (pointer: coarse)',
    );
    expect(dialog.open).toBe(true);
    expect(component.menuOpen()).toBe(true);
  });

  it('keeps a menu opened again when the slide of an earlier closing ends', () => {
    // A second Escape makes the browser close the menu at once, while its slide out still runs.
    // If the reader opens the menu again before that slide ends, its end must not close it.
    const dialog = mockDialog();
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({ matches: false })),
    );
    const slideEnds: Array<() => void> = [];
    dialog.animate = vi.fn(() => {
      const finishListeners: Array<() => void> = [];
      slideEnds.push(() => finishListeners.forEach((listener) => listener()));
      const animation = {
        addEventListener: (type: string, listener: () => void) => {
          if (type === 'finish') {
            finishListeners.push(listener);
          }
        },
        cancel: vi.fn(),
      };
      return animation as unknown as Animation;
    });
    const opener: HTMLButtonElement = fixture.nativeElement.querySelector('.menu-button');

    opener.click();
    component.closeMenu();
    const slideOutEnd = slideEnds[slideEnds.length - 1];
    dialog.close();
    opener.click();
    slideOutEnd();

    expect(dialog.open).toBe(true);
    expect(component.menuOpen()).toBe(true);
  });
});
