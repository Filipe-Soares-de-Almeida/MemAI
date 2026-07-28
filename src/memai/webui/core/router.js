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

import { $, esc } from './dom.js';
import { t } from '../i18n.js';
import { teardownView } from './lifecycle.js';
import { closeCtxMenu } from './ui.js';

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

export async function route() {
  const mine = ++generation;
  const { name, params } = parseHash();
  currentView = name;
  document.querySelectorAll('.nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.view === name));
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
    view.innerHTML = `<div class="empty">${t('error.loadFailed', { msg: esc(err.message) })}</div>`;
  }
  if (ctx.stale()) return;
  view.scrollTop = 0;
  /* deep link: #/any-view?record=<uid> opens the record drawer on top */
  if (params.get('record')) onRecord?.(params.get('record'));
}

export function refreshBehind() { route().catch(() => {}); }
