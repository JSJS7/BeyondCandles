import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { SidebarComponent } from '../sidebar/sidebar';
import { TopbarComponent } from '../topbar/topbar';

@Component({
  selector: 'app-layout',
  imports: [RouterOutlet, SidebarComponent, TopbarComponent],
  templateUrl: './layout.html',
  styleUrl: './layout.css',
})
export class LayoutComponent {}
