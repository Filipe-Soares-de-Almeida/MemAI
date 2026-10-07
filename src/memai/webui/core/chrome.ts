/* The chrome every view shares, mounted once into the shell: the toast stack in #toasts. */

import { createApp } from 'vue';
import type { App } from 'vue';
import ToastHost from '../components/ToastHost.vue';
import { resetToasts } from './toasts.ts';

let toastApp: App | null = null;

export function mountChrome(): void {
  const host = document.getElementById('toasts');
  if (!host) return;
  toastApp?.unmount();
  resetToasts();
  toastApp = createApp(ToastHost);
  toastApp.mount(host);
}
