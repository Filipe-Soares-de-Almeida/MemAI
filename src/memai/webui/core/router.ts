/* Hash router. It imports no view: app.js hands over the view table through registerViews().
   Renders are generation-counted; ctx.stale() tells a view someone navigated past it. */

import { $ } from './dom.ts';
import { teardownView } from './lifecycle.ts';
import { closeCtxMenu, modalOpen } from './ui.js';
import { failedHTML } from './shared.js';
import { mountView } from './vue.ts';
import type { Component } from 'vue';

export interface ViewContext { stale: () => boolean }
export type RenderView = (host: HTMLElement, params: URLSearchParams, ctx: ViewContext) => unknown;
/* a render function that draws into the host, or a Vue component mounted there */
export type View = RenderView | Component;
export type Params = Record<string, string>;
type RecordHook = (uid: string) => void;

let VIEWS: Record<string, View> = {};
let onRecord: RecordHook | null = null;

/* Views laid out as window-filling panes instead of a scrolling page. */
const FILLS = new Set(['memories', 'diagrams', 'domains', 'memory', 'maintenance',
                       'optimization']);

export function registerViews(map: Record<string, View>,
                              { onRecord: recordHook = null }: { onRecord?: RecordHook | null } = {}): void {
  VIEWS = map;
  onRecord = recordHook;
}

export function parseHash(): { name: string; params: URLSearchParams } {
  const h = location.hash.replace(/^#\/?/, '');
  const [name, qs] = h.split('?');
  return { name: VIEWS[name] ? name : 'overview', params: new URLSearchParams(qs || '') };
}

export function go(view: string, params: Params = {}): void {
  const qs = new URLSearchParams(params).toString();
  location.hash = `#/${view}${qs ? '?' + qs : ''}`;
}

/* Rewrite the current route's params without navigating, for a change the view already applied.
   `lastHash` moves too, or the next navigation would treat this hash as the one to go back to. */
export function replaceParams(view: string, params: Params = {}): void {
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
export function previousRoute(): { name: string; hash: string; qs: string } {
  const [name, qs] = previous.replace(/^#\/?/, '').split('?');
  return { name: VIEWS[name] ? name : '', hash: previous, qs: qs || '' };
}

/* Back to `view`: history.back() when that is where you came from, keeping its state; otherwise
   the view opens fresh. */
export function backTo(view: string, params: Params = {}): void {
  if (previousRoute().name === view) history.back();
  else go(view, params);
}

let generation = 0;
let currentView = '';
let lastHash = '';

/* `focus` moves the caret into the new view; refreshBehind() passes false so it never steals
   focus from an open drawer. */
export async function route({ focus = true }: { focus?: boolean } = {}): Promise<void> {
  const mine = ++generation;
  const { name, params } = parseHash();
  /* only a real navigation moves the trail; refreshBehind() re-runs the same hash */
  if (location.hash !== lastHash) { previous = lastHash; lastHash = location.hash; }
  currentView = name;
  document.querySelectorAll<HTMLElement>('.nav a').forEach(a => {
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
  if (!view) throw new Error('the shell has no #view');
  /* the diagram editor runs full-bleed: see .view.wide */
  view.classList.toggle('wide', name === 'diagram');
  /* a list beside its inspector fills the window and scrolls inside its own
     panes rather than as a page: see .view.fill */
  view.classList.toggle('fill', FILLS.has(name));
  view.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  const ctx: ViewContext = { stale: () => mine !== generation };
  try {
    const entry = VIEWS[name];
    if (typeof entry === 'function') await (entry as RenderView)(view, params, ctx);
    else await mountView(entry, view, params, ctx);
  } catch (err) {
    if (ctx.stale()) return;
    /* Not `.empty`: a failed load must not look like an empty store. Retry re-runs this route. */
    view.innerHTML = failedHTML(err);
    view.querySelector('[data-retry]')?.addEventListener('click', () => route({ focus: false }));
  }
  if (ctx.stale()) return;
  view.scrollTop = 0;
  /* Caret into the new view (tabindex="-1", no ring) so screen readers announce it; skipped
     under a modal and when the view already placed the caret. */
  if (focus && !modalOpen() && !view.contains(document.activeElement))
    view.focus({ preventScroll: true });
  /* `record` is a legacy deep-link param: it names a record to open over
     whichever view the address asked for. */
  const record = params.get('record');
  if (record) onRecord?.(record);
}

export function refreshBehind(): void { route({ focus: false }).catch(() => {}); }
