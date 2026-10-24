import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { LayoutComponent } from './layout';

describe('Layout', () => {
  let component: LayoutComponent;
  let fixture: ComponentFixture<LayoutComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LayoutComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(LayoutComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    TestBed.inject(HttpTestingController).expectOne('http://127.0.0.1:8000/stocks').flush([]);
    await fixture.whenStable();
  });

  afterEach(() => TestBed.inject(HttpTestingController).verify());

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

  it('closes an open mobile menu when the viewport changes to desktop width', () => {
    const dialog = mockDialog();
    const opener: HTMLButtonElement = fixture.nativeElement.querySelector('.menu-button');
    opener.click();
    component.onViewportResize();
    expect(dialog.open).toBe(false);
    expect(component.menuOpen()).toBe(false);
  });

  it('cycles Tab and Shift+Tab inside the modal instead of moving focus behind it', () => {
    const dialog = mockDialog();
    fixture.nativeElement.querySelector('.menu-button').click();
    const closeButton = dialog.querySelector<HTMLButtonElement>('.menu-close');
    for (const shiftKey of [false, true]) {
      const key = new KeyboardEvent('keydown', {
        key: 'Tab',
        shiftKey,
        bubbles: true,
        cancelable: true,
      });
      closeButton?.dispatchEvent(key);
      expect(key.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(closeButton);
    }
  });
});
