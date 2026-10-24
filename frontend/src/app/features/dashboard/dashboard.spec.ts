import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Dashboard } from './dashboard';

describe('Dashboard', () => {
  let component: Dashboard;
  let fixture: ComponentFixture<Dashboard>;
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Dashboard],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(Dashboard);
    component = fixture.componentInstance;
    http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => http.verify());

  it('shows a healthy backend using a controlled response without a running server', () => {
    http.expectOne('http://127.0.0.1:8000/health').flush({ status: 'ok' });
    fixture.detectChanges();

    expect(component).toBeTruthy();
    expect(fixture.nativeElement.textContent).toContain('Backend is running');
  });

  it('shows an unavailable backend when the health request fails', () => {
    http.expectOne('http://127.0.0.1:8000/health').flush(
      {},
      {
        status: 503,
        statusText: 'Service Unavailable',
      },
    );
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Backend is unavailable');
  });
});
