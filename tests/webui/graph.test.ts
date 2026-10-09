import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';
import GraphView from '../../src/memai/webui/views/graph/GraphView.vue';
import { graphParams, initialMode, initialShow } from '../../src/memai/webui/views/graph/graph.ts';
import { GraphCanvas } from '../../src/memai/webui/engines/graph-2d.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');
const settle = async () => { for (let i = 0; i < 4; i++) { await nextTick(); await new Promise(r => setTimeout(r, 0)); } };

/* happy-dom draws nothing; a context that answers every call lets the engine start */
const stubCtx = () => new Proxy({} as Record<string | symbol, unknown>, {
  get: (o, k) => (k in o ? o[k] : k === 'measureText' ? (s: string) => ({ width: String(s).length * 6 }) : () => {}),
  set: (o, k, v) => { o[k] = v; return true; },
});
let refuse = false;
HTMLCanvasElement.prototype.getContext = function () { return refuse ? null : stubCtx(); } as never;

beforeEach(() => { refuse = false; localStorage.removeItem('memai.graph'); });
afterEach(teardownView);

const NODES = [
  { uid: 'a1', type: 'note', domain: 'acme/lamps', status: 'active', confidence: 'unverified', tags: '',
    title: 'Lamp oil', label: '', degree: 1, created_at: '' },
  { uid: 'a2', type: 'anti_pattern', domain: 'acme/lamps', status: 'active', confidence: 'confirmed', tags: '',
    title: 'Brimful lantern', label: '', degree: 1, created_at: '' },
];
const EDGES = [{ id: 1, from_uid: 'a1', to_uid: 'a2', relation_type: 'relates_to', note: '' }];

async function show(params: Record<string, string> = {}) {
  serveApi((path: string) => {
    if (path === '/api/domains') return { domains: [] };
    if (path.startsWith('/api/graph')) return { nodes: NODES, edges: EDGES, total: 2, truncated: false };
    if (path === '/api/relations') return { ok: true, id: 2 };
    throw new Error(`unexpected ${path}`);
  });
  const view = document.getElementById('view') as HTMLElement;
  await mountView(GraphView, view, new URLSearchParams(params), { stale: () => false });
  await settle();
  return view;
}

describe('the graph canvas', () => {
  it('glides to fit and to a memory even when the system and an old stored choice ask for less motion', async () => {
    const asked = window.matchMedia;
    window.matchMedia = (() => ({ matches: true, addEventListener() {} })) as never;
    localStorage.setItem('memai.motion', 'never');
    vi.resetModules();
    const { GraphCanvas: Fresh } = await import('../../src/memai/webui/engines/graph-2d.ts');
    const glides: number[] = [];
    const canvas = Object.assign(Object.create(Fresh.prototype), {
      cam: { k: 1, frame: (_: unknown, ms: number) => glides.push(ms),
             goTo: (_x: number, _y: number, _k: number, ms: number) => glides.push(ms) },
      arr: { box: () => ({}), locate: () => ({ x: 0, y: 0 }) },
      _wake() {},
    });
    canvas.fit();
    canvas.travel('u1');
    localStorage.removeItem('memai.motion');
    window.matchMedia = asked;
    expect(glides).toHaveLength(2);
    expect(glides.every(ms => ms > 0)).toBe(true);
  });
});

describe('the graph settings', () => {
  it('lets the address name the arrangement over the stored one, and falls back to the default', () => {
    expect(initialMode('pack', { mode: 'atlas' })).toBe('pack');
    expect(initialMode('nonsense', { mode: 'atlas' })).toBe('atlas');
    expect(initialMode(null, {})).toBe('hubs');
  });

  it('draws names and leaves relations off unless told, and reads an old record for its names only', () => {
    expect(initialShow({})).toEqual({ links: false, domains: true, names: true });
    expect(initialShow({ links: true, names: false })).toEqual({ links: true, domains: true, names: false });
    expect(initialShow({ titles: false, links: true })).toEqual({ links: false, domains: false, names: false });
  });

  it('leaves every default out of the address', () => {
    expect(graphParams({ status: 'active', domain: '', type: '', mode: 'hubs' })).toEqual({});
    expect(graphParams({ status: '', domain: 'acme', type: 'note', mode: 'pack' }))
      .toEqual({ status: '', domain: 'acme', type: 'note', mode: 'pack' });
  });
});

describe('the graph view', () => {
  it('labels its filters like the memory list, above a canvas that sits in the view wrapper', async () => {
    const view = await show();
    const labels = [...view.querySelectorAll('.graph-bar .tb-field .mg-label')].map(l => l.textContent);
    expect(labels).toEqual(['g.f.find', 'mem.f.domain', 'mem.f.type', 'mem.f.status', 'g.f.show'].map(k => en[k]));
    expect(view.querySelector(':scope > .anim > .graph-wrap#gWrap > canvas#gGl')).not.toBeNull();
  });

  it('keeps a toggle and a picked arrangement for the next visit, and writes the arrangement to the address', async () => {
    const view = await show();
    (view.querySelector('#gShowLinks') as HTMLElement).click();
    await settle();
    expect(view.querySelector('#gShowLinks')?.getAttribute('aria-pressed')).toBe('true');
    expect(JSON.parse(localStorage.getItem('memai.graph') || '{}')).toMatchObject({ links: true });

    (view.querySelector('#gMode') as HTMLElement).click();
    (document.querySelector('.pick-pop [role="option"][data-v="atlas"]') as HTMLElement).click();
    await settle();
    expect(location.hash).toBe('#/graph?mode=atlas');
    expect(JSON.parse(localStorage.getItem('memai.graph') || '{}')).toMatchObject({ mode: 'atlas', links: true });
    expect(view.querySelector('#gLegendNote')?.textContent).toBe(en['g.mode.atlas.note']);
  });

  it('shows the selected memory with its relations to travel, and closes it', async () => {
    let engine: GraphCanvas | undefined;
    const setMode = GraphCanvas.prototype.setMode;
    GraphCanvas.prototype.setMode = function (this: GraphCanvas, ...a: Parameters<GraphCanvas['setMode']>) {
      engine = this;
      return setMode.apply(this, a);
    };
    const view = await show();
    GraphCanvas.prototype.setMode = setMode;
    engine?.select('a1');
    await settle();
    const card = view.querySelector('#gCard') as HTMLElement;
    expect(card.hidden).toBe(false);
    expect(card.querySelector('.gc-name')?.textContent).toBe('Lamp oil');
    expect(card.querySelector('[data-hop="a2"]')).not.toBeNull();
    (card.querySelector('[data-shut]') as HTMLElement).click();
    await settle();
    expect(card.hidden).toBe(true);
  });

  it('creates the relation link mode drew, with the type and note given', async () => {
    let engine: GraphCanvas | undefined;
    const setMode = GraphCanvas.prototype.setMode;
    GraphCanvas.prototype.setMode = function (this: GraphCanvas, ...a: Parameters<GraphCanvas['setMode']>) {
      engine = this;
      return setMode.apply(this, a);
    };
    const view = await show();
    GraphCanvas.prototype.setMode = setMode;
    (view.querySelector('#gLink') as HTMLElement).click();
    await settle();
    expect(view.querySelector('#gBanner')?.hasAttribute('hidden')).toBe(false);
    engine?.cb.onLink('pair', engine.byUid.get('a1')!, engine.byUid.get('a2')!);
    await settle();
    (document.querySelector('#glNote') as HTMLInputElement).value = 'same lamp';
    document.querySelector('#glNote')?.dispatchEvent(new Event('input'));
    (document.querySelector('.modal [data-ok]') as HTMLElement).click();
    await settle();
    expect(calls.find(c => c.path === '/api/relations')?.body)
      .toEqual({ from_uid: 'a1', to_uid: 'a2', relation_type: 'relates_to', note: 'same lamp' });
    expect(document.querySelector('.modal')).toBeNull();
  });

  it('says why when the browser refuses a canvas, and offers the list instead', async () => {
    refuse = true;
    const view = await show();
    expect(view.querySelector('#gGl')).toBeNull();
    expect(view.querySelector('.graph-blocked')?.textContent).toContain(en['g.startFailed']);
    (view.querySelector('#gToList') as HTMLElement).click();
    expect(location.hash).toBe('#/memories');
  });
});
