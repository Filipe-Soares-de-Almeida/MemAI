/* Hash router.

   This module deliberately imports no view. app.js owns the view table
   and hands it over through registerViews() -- otherwise router and views
   would import each other, and the deep-link hook (open a record drawer
   on top of any view) would drag the drawer in here too.

   Renders are generation-counted. Two navigations in quick succession
   used to race: both awaited their API call and both wrote to #view, so
   the SLOWER one painted last and the address bar disagreed with the
   screen. A render is now handed a ctx whose stale() says "someone has
   navigated past you, stop" -- checked by each view after its awaits and
   before its first write, and again here around the post-render steps. */

import { $ } from './dom.js';
import { t } from '../i18n.js';
import { teardownView } from './lifecycle.js';
import { closeCtxMenu, modalOpen } from './ui.js';
import { failedHTML } from './shared.js';

let VIEWS = {};
let onRecord = null;

/* The views laid out as panes that fill the window instead of as a page
   that scrolls. Named here rather than by each view, because it is the
   shell's box they are filling and the shell is what has to be told. */
const FILLS = new Set(['memories', 'domains', 'memory', 'maintenance', 'optimization']);

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

/* The hash the last route ran on. Kept so a view can go BACK to the one it
   came from with whatever that one was filtered and paged to -- which
   go(view) cannot do, because it would land on an unfiltered first page. */
let previous = '';

/* The route the last navigation came FROM, as {name, hash}. `name` is ''
   when there is nothing behind this one -- a reload straight onto a view,
   or the first paint. A view uses it to offer its own way back instead of
   leaving the browser button as the only one. */
export function previousRoute() {
  const [name, qs] = previous.replace(/^#\/?/, '').split('?');
  return { name: VIEWS[name] ? name : '', hash: previous, qs: qs || '' };
}

/* Back to `view`, keeping its state when that is where you came from.
   history.back() replays the exact URL, filters and page included; when the
   previous entry is something else -- a record opened from a link, a reload
   straight onto one -- there is nothing to replay and this opens the view
   fresh. */
export function backTo(view, params = {}) {
  if (previousRoute().name === view) history.back();
  else go(view, params);
}

let generation = 0;
let currentView = '';
let lastHash = '';

export const activeView = () => currentView;

/* `focus` moves the caret into the new view, which is right for a
   navigation and wrong for refreshBehind() -- that one repaints the view
   under an open drawer, and stealing focus out of the drawer mid-edit is
   exactly what it must not do. */
export async function route({ focus = true } = {}) {
  const mine = ++generation;
  const { name, params } = parseHash();
  /* only a real navigation moves the trail: refreshBehind() re-runs this
     on the same hash, and treating that as a step would make Back return to
     the record you are already on */
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
    /* Not `.empty`: a view that failed to load is not a view with nothing in
       it, and rendering both the same way meant a dropped connection was
       indistinguishable from an empty store. Retry re-runs this same route. */
    view.innerHTML = failedHTML(err);
    view.querySelector('[data-retry]').addEventListener('click', () => route({ focus: false }));
  }
  if (ctx.stale()) return;
  view.scrollTop = 0;
  /* Put the caret in what was just navigated to. Without this the focus
     stays on the rail link that was pressed: a screen reader announces
     nothing, and Tab walks the rail again instead of entering the view.
     #view is tabindex="-1" for exactly this, and programmatic focus on it
     draws no ring. Never while something is layered over the view -- which
     since the record became a dialog is one check rather than two -- and
     never over a view that already put the caret somewhere inside itself:
     `/` asks Memories for its search field, and this used to take it
     straight back. A view that has aimed the caret has aimed it better. */
  if (focus && !modalOpen() && !view.contains(document.activeElement))
    view.focus({ preventScroll: true });
  /* The record used to open as a dialog over whatever was showing, so a deep
     link to one was a param on the covered view. It has an address of its own
     now; the param is kept as a redirect so a bookmark still lands on it. */
  if (params.get('record')) onRecord?.(params.get('record'));
}

export function refreshBehind() { route({ focus: false }).catch(() => {}); }
