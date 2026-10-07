import { afterEach, beforeEach, vi } from 'vitest';
import { fakeFetch, serveApi } from './support.js';

vi.stubGlobal('fetch', fakeFetch);

/* the hosts index.html gives every view: the view, the dialogs, toasts and the tip */
const SHELL = `<main id="view" class="view" tabindex="-1"></main>
  <div id="modalRoot"></div>
  <div id="toasts" class="toasts" aria-live="polite"></div>
  <div id="tip" class="tip" hidden></div>`;

/* imported here rather than at the top, so the catalogs it loads go through the fetch above */
beforeEach(async () => {
  document.body.innerHTML = SHELL;
  (await import('../../src/memai/webui/core/chrome.ts')).mountChrome();
});

afterEach(() => { serveApi(null); });
