/* Hash router. It imports no view: app.js hands over the view table through registerViews().
   Renders are generation-counted; ctx.stale() tells a view someone navigated past it. */

import { $ } from './dom.js';
import { teardownView } from './lifecycle.js';
import { closeCtxMenu, modalOpen } from './ui.js';
import { failedHTML } from './shared.js';

let VIEWS = {};
let onRecord = null;

/* Views laid out as window-filling panes instead of a scrolling page. */
const FILLS = new Set(['memories', 'diagrams', 'domains', 'memory', 'maintenance',
                       'optimization']);

export function registerViews(map, { onRecord: recordHook = null } = {}) {
  VIEWS = map;
  onRecord = recordHook;
}

export function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [name, qs] = h.split('?');
  return { name: VIEWS[name] ? name : 'overview', params: new URLSearchParams(qs || '') };
}

export function go(view, params = {}) {
  const qs = new URLSearchParams(params).toString();
  location.hash = `#/${view}${qs ? '?' + qs : ''}`;
}

/* Rewrite the current route's params without navigating, for a change the view already applied.
   `lastHash` moves too, or the next navigation would treat this hash as the one to go back to. */
export function replaceParams(view, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const hash = `#/${view}${qs ? '?' + qs : ''}`;
  if (hash === location.hash) return;
  history.replaceState(history.state, '', `${location.pathname}${location.search}${hash}`);
  lastHash = hash;
}

/* The hash the last route ran on, so a view can go back with its filters and page intact. */
let previous = '';

/* The route the last navigation came from, as {name, hash}; `name` is '' when nothing is behind
   this one. */
export function previousRoute() {
  const [name, qs] = previous.replace(/^#\/?/, '').split('?');
  return { name: VIEWS[name] ? name : '', hash: previous, qs: qs || '' };
}

/* Back to `view`: history.back() when that is where you came from, keeping its state; otherwise
   the view opens fresh. */
export function backTo(view, params = {}) {
  if (previousRoute().name === view) history.back();
  else go(view, params);
}

let generation = 0;
let currentView = '';
let lastHash = '';

/* `focus` moves the caret into the new view; refreshBehind() passes false so it never steals
   focus from an open drawer. */
export async function route({ focus = true } = {}) {
  const mine = ++generation;
  const { name, params } = parseHash();
  /* only a real navigation moves the trail; refreshBehind() re-runs the same hash */
  if (location.hash !== lastHash) { previous = lastHash; lastHash = location.hash; }
  currentView = name;
  document.querySelectorAll('.nav a').forEach(a => {
    /* aria-current is also the styling hook (see admin.css): one attribute,
       so the bar cannot show one section and announce another */
    if (a.dataset.view === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  /* whatever the outgoing view parked outside #view -- canvas engines
     listening on window, the bulk bar on document.body */
  teardownView();
  closeCtxMenu();   /* it lives on document.body, so the view swap misses it */
  const view = $('#view');
  /* the diagram editor runs full-bleed: see .view.wide */
  view.classList.toggle('wide', name === 'diagram');
  /* a list beside its inspector fills the window and scrolls inside its own
     panes rather than as a page: see .view.fill */
  view.classList.toggle('fill', FILLS.has(name));
  view.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  const ctx = { stale: () => mine !== generation };
  try {
    await VIEWS[name](view, params, ctx);
  } catch (err) {
    if (ctx.stale()) return;
    /* Not `.empty`: a failed load must not look like an empty store. Retry re-runs this route. */
    view.innerHTML = failedHTML(err);
    view.querySelector('[data-retry]').addEventListener('click', () => route({ focus: false }));
  }
  if (ctx.stale()) return;
  view.scrollTop = 0;
  /* Caret into the new view (tabindex="-1", no ring) so screen readers announce it; skipped
     under a modal and when the view already placed the caret. */
  if (focus && !modalOpen() && !view.contains(document.activeElement))
    view.focus({ preventScroll: true });
  /* `record` is a legacy deep-link param: it names a record to open over
     whichever view the address asked for. */
  if (params.get('record')) onRecord?.(params.get('record'));
}

export function refreshBehind() { route({ focus: false }).catch(() => {}); }
