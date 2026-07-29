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

let generation = 0;
let currentView = '';

export const activeView = () => currentView;

/* `focus` moves the caret into the new view, which is right for a
   navigation and wrong for refreshBehind() -- that one repaints the view
   under an open drawer, and stealing focus out of the drawer mid-edit is
   exactly what it must not do. */
export async function route({ focus = true } = {}) {
  const mine = ++generation;
  const { name, params } = parseHash();
  currentView = name;
  document.querySelectorAll('.nav a').forEach(a => {
    /* aria-current is also the styling hook (see admin.css): one attribute,
       so the rail cannot show one section and announce another */
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
     draws no ring. Never while something is layered over the view, and never
     over a view that already put the caret somewhere inside itself -- `/`
     asks Memories for its search field, and this used to take it straight
     back. A view that has aimed the caret has aimed it better than this can. */
  if (focus && !modalOpen() && $('#drawer').hidden && !view.contains(document.activeElement))
    view.focus({ preventScroll: true });
  /* deep link: #/any-view?record=<uid> opens the record drawer on top */
  if (params.get('record')) onRecord?.(params.get('record'));
}

export function refreshBehind() { route({ focus: false }).catch(() => {}); }
