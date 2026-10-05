import { afterEach, beforeEach, vi } from 'vitest';
import { fakeFetch, serveApi } from './support.js';

vi.stubGlobal('fetch', fakeFetch);

/* the hosts index.html gives every view: the view, the dialogs, toasts and the tip */
const SHELL = `<main id="view" class="view" tabindex="-1"></main>
  <div id="modalRoot"></div>
  <div id="toasts" class="toasts" aria-live="polite"></div>
  <div id="tip" class="tip" hidden></div>`;

beforeEach(() => { document.body.innerHTML = SHELL; });

afterEach(() => { serveApi(null); });
